"""Tests for the Consciousness system and related events."""

from __future__ import annotations

import pytest

from auton.core.consciousness import Consciousness
from auton.core.events import InternalThought


@pytest.fixture
def consciousness(tmp_path):
    """Return a Consciousness instance backed by a temporary database."""
    db_path = tmp_path / "consciousness.db"
    return Consciousness(db_path=str(db_path), max_memories=1000)


class TestStreamOfConsciousness:
    def test_empty_consciousness(self, consciousness: Consciousness) -> None:
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "still forming my thoughts" in result

    def test_with_trade_memory(self, consciousness: Consciousness) -> None:
        consciousness.remember(
            "trade_executed",
            {"action": "trade", "symbol": "BTCUSDT", "amount": 0.01},
            importance=0.5,
        )
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "market activity" in result

    def test_with_balance_loss(self, consciousness: Consciousness) -> None:
        consciousness.remember(
            "balance_changed",
            {"old_balance": 50.0, "new_balance": 45.0},
            importance=0.6,
        )
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "took a hit" in result

    def test_with_balance_gain(self, consciousness: Consciousness) -> None:
        consciousness.remember(
            "balance_changed",
            {"old_balance": 50.0, "new_balance": 55.0},
            importance=0.6,
        )
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "encouraged" in result

    def test_with_decision(self, consciousness: Consciousness) -> None:
        dec_id = consciousness.record_decision(
            action="buy BTC", strategy="momentum", expected_roi=0.05,
            confidence=0.7, risk_score=0.3, budget=5.0,
        )
        consciousness.resolve_decision(dec_id, "failure", actual_return=-1.0, notes="stop hit")
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "loss" in result
        assert "tighten" in result or "switch" in result

    def test_with_pending_decision(self, consciousness: Consciousness) -> None:
        consciousness.record_decision(
            action="sell ETH", strategy="mean_reversion", expected_roi=0.03,
            confidence=0.6, risk_score=0.2, budget=3.0,
        )
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "pending" in result

    def test_with_strategy_stats(self, consciousness: Consciousness) -> None:
        consciousness.update_strategy_performance(
            "momentum", is_win=True, pnl=5.0, roi=0.1, risk=0.3,
        )
        consciousness.update_strategy_performance(
            "arbitrage", is_loss=True, pnl=-2.0, roi=-0.05, risk=0.2,
        )
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "momentum" in result
        assert "arbitrage" in result

    def test_with_learning(self, consciousness: Consciousness) -> None:
        consciousness.record_learning(
            insight="BTC dips precede weekend rallies",
            domain="pattern",
            confidence=0.6,
            source="observation",
        )
        result = consciousness.stream_of_consciousness(n_recent=10)
        assert "learning" in result or "pattern" in result


class TestDream:
    def test_empty_dream(self, consciousness: Consciousness) -> None:
        ideas = consciousness.dream()
        # Always returns at least the resource-efficiency idea
        assert len(ideas) >= 1
        assert all("idea" in idea and "confidence" in idea and "rationale" in idea for idea in ideas)

    def test_dream_with_strategies(self, consciousness: Consciousness) -> None:
        consciousness.update_strategy_performance(
            "momentum", is_win=True, pnl=3.0, roi=0.05, risk=0.3,
        )
        consciousness.update_strategy_performance(
            "mean_reversion", is_win=True, pnl=2.0, roi=0.04, risk=0.2,
        )
        ideas = consciousness.dream()
        assert len(ideas) >= 1
        # Should include hybrid strategy idea
        assert any("combine" in idea["idea"] for idea in ideas)

    def test_dream_stores_memories(self, consciousness: Consciousness) -> None:
        ideas = consciousness.dream()
        memories = consciousness.recall(event_type="dream_idea")
        assert len(memories) == len(ideas)

    def test_dream_with_learnings(self, consciousness: Consciousness) -> None:
        consciousness.record_learning(
            insight="Stop-losses saved capital three times",
            domain="strategy",
            confidence=0.8,
            source="review",
        )
        ideas = consciousness.dream()
        assert any("failure" in idea["rationale"] or "simulate" in idea["idea"] for idea in ideas)


class TestProactiveThought:
    def test_returns_string_or_none(self, consciousness: Consciousness) -> None:
        result = consciousness.proactive_thought()
        assert result is None or isinstance(result, str)

    def test_entropy_gate(self, consciousness: Consciousness) -> None:
        # With the 0.6 entropy gate, we should see a mix, but calling many
        # times statistically guarantees at least one hit.
        results = [consciousness.proactive_thought() for _ in range(50)]
        strings = [r for r in results if r is not None]
        assert len(strings) > 0
        assert all(isinstance(s, str) for s in strings)

    def test_survival_context(self, consciousness: Consciousness) -> None:
        consciousness.remember(
            "survival_mode",
            {"runway_hours": 2.0, "balance": 10.0, "burn_rate": 5.0},
            importance=0.9,
        )
        # Force multiple calls to beat entropy gate
        for _ in range(20):
            result = consciousness.proactive_thought()
            if result and "survival" in result:
                assert "smallest" in result or "safest" in result
                return
        pytest.skip("Random entropy gate did not trigger survival prompt in 20 tries")

    def test_tier_drop_context(self, consciousness: Consciousness) -> None:
        consciousness.remember(
            "tier_changed",
            {"old_tier": 2, "new_tier": 1, "balance": 80.0},
            importance=0.6,
        )
        for _ in range(20):
            result = consciousness.proactive_thought()
            if result and "dropped a tier" in result:
                assert "capability" in result
                return
        pytest.skip("Random entropy gate did not trigger tier-drop prompt in 20 tries")

    def test_stores_memory(self, consciousness: Consciousness) -> None:
        # Call repeatedly to ensure at least one non-None result
        for _ in range(50):
            result = consciousness.proactive_thought()
            if result:
                break
        memories = consciousness.recall(event_type="proactive_thought")
        assert len(memories) >= 1


class TestInternalThoughtEvent:
    def test_fields(self) -> None:
        event = InternalThought(thought="What if I tried arbitrage?", source="consciousness")
        assert event.thought == "What if I tried arbitrage?"
        assert event.source == "consciousness"
        assert event.timestamp is not None

    def test_defaults(self) -> None:
        event = InternalThought(thought="I wonder...")
        assert event.source == "consciousness"
        assert event.timestamp is not None

    def test_frozen(self) -> None:
        event = InternalThought(thought="test")
        with pytest.raises(AttributeError):
            event.thought = "changed"  # type: ignore[misc]
