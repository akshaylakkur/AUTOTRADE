"""Tests for the ÆON Simulation Engine."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from auton.ledger.exceptions import InsufficientFundsError, LedgerError
from auton.simulation.analyzer import SimulationAnalyzer, SimulationMetrics
from auton.simulation.clock import SimulationClock
from auton.simulation.recorder import RecordedEvent, SimulationRecorder
from auton.simulation.session import SimulationConfig, SimulationSession
from auton.simulation.wallet import SimulatedWallet


# =============================================================================
# SimulatedWallet
# =============================================================================
class TestSimulatedWallet:
    def test_initial_balance_zero(self) -> None:
        w = SimulatedWallet()
        assert w.get_balance() == 0.0

    def test_initial_balance_seed(self) -> None:
        w = SimulatedWallet(initial_balance=50.0)
        assert w.get_balance() == 50.0
        assert w.get_transaction_count() == 1

    def test_credit_increases_balance(self) -> None:
        w = SimulatedWallet()
        receipt = w.credit(10.0, "test")
        assert w.get_balance() == 10.0
        assert receipt.amount == 10.0
        assert receipt.reason == "test"
        assert receipt.running_balance == 10.0
        assert receipt.id == 1

    def test_debit_decreases_balance(self) -> None:
        w = SimulatedWallet(initial_balance=50.0)
        receipt = w.debit(20.0, "cost")
        assert w.get_balance() == 30.0
        assert receipt.amount == 20.0
        assert receipt.running_balance == 30.0

    def test_debit_insufficient_funds(self) -> None:
        w = SimulatedWallet(initial_balance=10.0)
        with pytest.raises(InsufficientFundsError):
            w.debit(20.0, "too much")

    def test_credit_non_positive(self) -> None:
        w = SimulatedWallet()
        with pytest.raises(LedgerError):
            w.credit(0.0, "bad")
        with pytest.raises(LedgerError):
            w.credit(-5.0, "bad")

    def test_debit_non_positive(self) -> None:
        w = SimulatedWallet(initial_balance=10.0)
        with pytest.raises(LedgerError):
            w.debit(0.0, "bad")
        with pytest.raises(LedgerError):
            w.debit(-5.0, "bad")

    def test_transaction_history_order(self) -> None:
        w = SimulatedWallet(initial_balance=0.0)
        w.credit(10.0, "a")
        w.credit(20.0, "b")
        w.debit(5.0, "c")
        history = list(w.get_transaction_history())
        assert len(history) == 3
        # newest first
        assert history[0].reason == "c"
        assert history[0].running_balance == 25.0
        assert history[1].reason == "b"
        assert history[2].reason == "a"

    def test_transaction_history_limit(self) -> None:
        w = SimulatedWallet(initial_balance=0.0)
        for i in range(10):
            w.credit(1.0, f"tx-{i}")
        history = list(w.get_transaction_history(limit=3))
        assert len(history) == 3

    def test_reset(self) -> None:
        w = SimulatedWallet(initial_balance=100.0)
        w.debit(30.0, "spend")
        assert w.get_balance() == 70.0
        w.reset(initial_balance=50.0)
        assert w.get_balance() == 50.0
        assert w.get_transaction_count() == 1
        w.reset()
        assert w.get_balance() == 0.0
        assert w.get_transaction_count() == 0


# =============================================================================
# SimulationClock
# =============================================================================
class TestSimulationClock:
    def test_default_start_is_nowish(self) -> None:
        c = SimulationClock()
        assert (datetime.now(timezone.utc) - c.now()).total_seconds() < 1

    def test_custom_start(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        assert c.now() == start

    def test_advance(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        c.set_tick_size(timedelta(minutes=5))
        t = c.advance()
        assert t == start + timedelta(minutes=5)
        assert c.now() == t

    def test_advance_custom_delta(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        t = c.advance(timedelta(hours=2))
        assert t == start + timedelta(hours=2)

    def test_fast_forward(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        c.pause()
        t = c.fast_forward(timedelta(days=1))
        assert t == start + timedelta(days=1)

    def test_rewind(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        c.advance(timedelta(hours=3))
        t = c.rewind(timedelta(hours=1))
        assert t == start + timedelta(hours=2)

    def test_set_time(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        new = datetime(2025, 6, 15, tzinfo=timezone.utc)
        assert c.set_time(new) == new

    def test_pause_blocks_advance(self) -> None:
        c = SimulationClock()
        c.pause()
        with pytest.raises(RuntimeError):
            c.advance()

    def test_resume_allows_advance(self) -> None:
        c = SimulationClock()
        c.pause()
        c.resume()
        c.advance()  # should not raise

    def test_is_paused(self) -> None:
        c = SimulationClock()
        assert not c.is_paused()
        c.pause()
        assert c.is_paused()

    def test_tick_size_must_be_positive(self) -> None:
        c = SimulationClock()
        with pytest.raises(ValueError):
            c.set_tick_size(timedelta(seconds=0))
        with pytest.raises(ValueError):
            c.set_tick_size(timedelta(seconds=-1))

    def test_reset(self) -> None:
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        c.advance(timedelta(days=5))
        c.pause()
        c.set_tick_size(timedelta(hours=2))
        t = c.reset(start=start)
        assert t == start
        assert not c.is_paused()
        assert c.get_tick_size() == timedelta(seconds=1)

    def test_isoformat(self) -> None:
        start = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        c = SimulationClock(start=start)
        assert c.isoformat() == "2020-01-01T12:00:00+00:00"


# =============================================================================
# SimulationRecorder
# =============================================================================
class TestSimulationRecorder:
    def test_empty(self) -> None:
        r = SimulationRecorder()
        assert r.get_event_count() == 0
        assert r.get_last_event() is None

    def test_record(self) -> None:
        r = SimulationRecorder()
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        ev = r.record(ts, "trade", "buy", {"symbol": "BTC"})
        assert isinstance(ev, RecordedEvent)
        assert ev.timestamp == ts
        assert ev.category == "trade"
        assert ev.action == "buy"
        assert ev.payload == {"symbol": "BTC"}

    def test_get_events_filtered(self) -> None:
        r = SimulationRecorder()
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        r.record(ts, "trade", "buy")
        r.record(ts, "trade", "sell")
        r.record(ts, "decision", "hold")
        assert r.get_event_count(category="trade") == 2
        assert r.get_event_count(action="sell") == 1
        assert r.get_event_count(category="decision", action="hold") == 1

    def test_get_last_event(self) -> None:
        r = SimulationRecorder()
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        r.record(ts, "a", "1")
        r.record(ts, "b", "2")
        last = r.get_last_event()
        assert last is not None
        assert last.category == "b"

    def test_reset(self) -> None:
        r = SimulationRecorder()
        ts = datetime(2020, 1, 1, tzinfo=timezone.utc)
        r.record(ts, "x", "y")
        r.reset()
        assert r.get_event_count() == 0


# =============================================================================
# SimulationAnalyzer
# =============================================================================
class TestSimulationAnalyzer:
    def test_empty(self) -> None:
        a = SimulationAnalyzer()
        m = a.compute()
        assert m == SimulationMetrics(
            total_pnl=0.0,
            total_return_pct=0.0,
            sharpe_ratio=0.0,
            max_drawdown=0.0,
            max_drawdown_pct=0.0,
            win_rate=0.0,
            win_count=0,
            loss_count=0,
            total_trades=0,
            avg_trade_pnl=0.0,
        )

    def test_total_pnl_and_win_rate(self) -> None:
        a = SimulationAnalyzer()
        a.add_returns([10.0, -5.0, 20.0, -3.0])
        m = a.compute()
        assert m.total_pnl == 22.0
        assert m.win_count == 2
        assert m.loss_count == 2
        assert m.total_trades == 4
        assert m.win_rate == 0.5
        assert m.avg_trade_pnl == 5.5

    def test_max_drawdown(self) -> None:
        a = SimulationAnalyzer()
        # peak at 10, drop to 5, recover to 15, drop to 8
        a.add_returns([10.0, -5.0, 10.0, -7.0])
        m = a.compute()
        assert m.max_drawdown == 7.0

    def test_sharpe_ratio(self) -> None:
        a = SimulationAnalyzer()
        # consistent positive returns
        a.add_returns([1.0, 1.1, 0.9, 1.05, 1.0])
        m = a.compute()
        assert m.sharpe_ratio > 0
        assert math.isfinite(m.sharpe_ratio)

    def test_zero_variance_sharpe(self) -> None:
        a = SimulationAnalyzer()
        a.add_returns([5.0, 5.0, 5.0])
        m = a.compute()
        assert m.sharpe_ratio == 0.0

    def test_from_trades_convenience(self) -> None:
        m = SimulationAnalyzer.from_trades([5.0, -2.0, 3.0])
        assert m.total_pnl == 6.0
        assert m.win_count == 2
        assert m.loss_count == 1

    def test_decimal_returns(self) -> None:
        from decimal import Decimal

        a = SimulationAnalyzer()
        a.add_return(Decimal("10.5"))
        a.add_return(Decimal("-2.5"))
        m = a.compute()
        assert m.total_pnl == 8.0

    def test_reset(self) -> None:
        a = SimulationAnalyzer()
        a.add_returns([1.0, 2.0])
        a.reset()
        assert a.compute().total_pnl == 0.0

    def test_all_losses(self) -> None:
        a = SimulationAnalyzer()
        a.add_returns([-1.0, -2.0, -3.0])
        m = a.compute()
        assert m.win_rate == 0.0
        assert m.win_count == 0
        assert m.loss_count == 3
        assert m.max_drawdown_pct == 0.0  # peak never exceeds 0


# =============================================================================
# SimulationSession
# =============================================================================
class TestSimulationSession:
    def test_default_config(self) -> None:
        s = SimulationSession()
        assert s.config.name == "unnamed"
        assert s.config.initial_balance == 50.0
        assert s.wallet.get_balance() == 50.0

    def test_custom_config(self) -> None:
        cfg = SimulationConfig(
            name="backtest-1",
            initial_balance=1000.0,
            start_time=datetime(2023, 6, 1, tzinfo=timezone.utc),
            tick_size=timedelta(minutes=5),
        )
        s = SimulationSession(cfg)
        assert s.config.name == "backtest-1"
        assert s.wallet.get_balance() == 1000.0
        assert s.clock.now() == cfg.start_time
        assert s.clock.get_tick_size() == timedelta(minutes=5)

    def test_start_stop(self) -> None:
        s = SimulationSession()
        assert not s.is_running()
        s.start()
        assert s.is_running()
        s.stop()
        assert not s.is_running()

    def test_start_records_event(self) -> None:
        s = SimulationSession()
        s.start()
        assert s.recorder.get_event_count(category="session", action="start") == 1

    def test_stop_records_event(self) -> None:
        s = SimulationSession()
        s.start()
        s.stop()
        ev = s.recorder.get_last_event()
        assert ev is not None
        assert ev.category == "session"
        assert ev.action == "stop"
        assert "final_balance" in ev.payload

    def test_advance(self) -> None:
        cfg = SimulationConfig(
            start_time=datetime(2023, 1, 1, tzinfo=timezone.utc),
            tick_size=timedelta(hours=1),
        )
        s = SimulationSession(cfg)
        t = s.advance()
        assert t == datetime(2023, 1, 1, 1, tzinfo=timezone.utc)

    def test_record(self) -> None:
        s = SimulationSession()
        s.record("trade", "buy", {"symbol": "ETH"})
        assert s.recorder.get_event_count(category="trade", action="buy") == 1

    def test_analyze_empty(self) -> None:
        s = SimulationSession()
        m = s.analyze()
        assert m.total_pnl == 0.0

    def test_reset(self) -> None:
        cfg = SimulationConfig(initial_balance=200.0)
        s = SimulationSession(cfg)
        s.start()
        s.wallet.debit(50.0, "cost")
        s.advance(timedelta(days=1))
        s.record("trade", "win", {"pnl": 10.0})
        s.analyzer.add_return(10.0)
        s.reset()
        assert not s.is_running()
        assert s.wallet.get_balance() == 200.0
        assert s.recorder.get_event_count() == 0
        assert s.analyzer.compute().total_pnl == 0.0
        assert s.clock.now() == cfg.start_time

    def test_full_run(self) -> None:
        cfg = SimulationConfig(
            name="integration",
            initial_balance=100.0,
            start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
            tick_size=timedelta(minutes=1),
        )
        s = SimulationSession(cfg)
        s.start()

        # simulate a few trades
        s.advance()
        s.wallet.debit(10.0, "position")
        s.record("trade", "open", {"cost": 10.0})

        s.advance(timedelta(minutes=5))
        s.wallet.credit(15.0, "close")
        s.record("trade", "close", {"revenue": 15.0})
        s.analyzer.add_return(5.0)

        s.stop()

        assert s.wallet.get_balance() == 105.0
        metrics = s.analyze()
        assert metrics.total_pnl == 5.0
        assert metrics.win_rate == 1.0

    def test_config_dict(self) -> None:
        cfg = SimulationConfig(
            name="test", initial_balance=10.0, metadata={"foo": "bar"}
        )
        s = SimulationSession(cfg)
        d = s._config_dict()
        assert d["name"] == "test"
        assert d["initial_balance"] == 10.0
        assert d["metadata"] == {"foo": "bar"}
