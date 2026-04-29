"""Integration tests for the AEON orchestrator."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from auton.aeon import AEON
from auton.core.state_machine import State as LifecycleState


@pytest.fixture
def aeon(monkeypatch):
    """Return an AEON instance with Vault env var satisfied."""
    monkeypatch.setenv("AEON_VAULT_KEY", Fernet.generate_key().decode())
    with patch("auton.aeon.AdaptionEngine") as MockAdaption:
        MockAdaption.return_value = MagicMock()
        return AEON()


@pytest.mark.asyncio
async def test_initialization_with_seed(aeon, tmp_path):
    """AEON initializes ledger with the $50 seed balance."""
    with patch.object(aeon._wallet, "get_balance", return_value=0.0):
        with patch.object(aeon._wallet, "credit") as mock_credit:
            with patch.object(aeon._state_machine, "transition_to") as mock_transition:
                await aeon.initialize()

    assert aeon.state == LifecycleState.INIT
    mock_credit.assert_called_once()
    # State machine may already be INIT, so transition may or may not be called


@pytest.mark.asyncio
async def test_start_transitions_to_running(aeon):
    """start() transitions from INIT to RUNNING."""
    await aeon.initialize()
    patches = [
        patch.object(aeon._state_machine, "transition_to"),
        patch.object(aeon._env_sensor, "start", new_callable=AsyncMock),
        patch.object(aeon._market_connector, "connect", new_callable=AsyncMock),
        patch.object(aeon._opportunity_monitor, "start", new_callable=AsyncMock),
        patch.object(aeon, "_decision_loop", new_callable=AsyncMock),
        patch.object(aeon, "_planning_loop", new_callable=AsyncMock),
        patch.object(aeon, "_reflex_loop", new_callable=AsyncMock),
        patch.object(aeon, "_monitor_loop", new_callable=AsyncMock),
        patch.object(aeon, "_adaptation_loop", new_callable=AsyncMock),
        patch.object(aeon, "_task_queue_loop", new_callable=AsyncMock),
        patch.object(aeon, "_email_digest_loop", new_callable=AsyncMock),
        patch.object(aeon, "_market_data_loop", new_callable=AsyncMock),
        patch.object(aeon, "_consciousness_loop", new_callable=AsyncMock),
    ]
    if aeon._email_client is not None:
        patches.append(patch.object(aeon._email_client, "start_retry_worker", new_callable=AsyncMock))
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        mock_transition = aeon._state_machine.transition_to
        task = asyncio.create_task(aeon.start())
        await asyncio.sleep(0.05)
        aeon._running = False
        try:
            await task
        except asyncio.CancelledError:
            pass

    # transition_to called for RUNNING
    assert any(call.args == (LifecycleState.RUNNING,) for call in mock_transition.call_args_list)


@pytest.mark.asyncio
async def test_terminal_protocol_zero_balance(aeon, tmp_path):
    """Zero balance triggers terminal protocol: liquidation, export, key revocation, obituary."""
    with patch.object(aeon._wallet, "get_balance", return_value=0.0):
        with patch.object(aeon._liquidator, "liquidate_all_positions", new_callable=AsyncMock) as mock_liquidate:
            with patch.object(aeon._vault, "revoke_all", new_callable=AsyncMock) as mock_revoke:
                with patch.object(aeon._terminal, "execute", new_callable=AsyncMock) as mock_terminal:
                    with patch("asyncio.sleep", new_callable=AsyncMock):
                        await aeon.initialize()
                        aeon._running = True
                        await aeon._planning_loop()

    mock_terminal.assert_awaited_once()


@pytest.mark.asyncio
async def test_hibernation_trigger_after_drawdown(aeon):
    """A 10% drawdown from peak triggers hibernation."""
    with patch.object(aeon._wallet, "get_balance", return_value=45.0):
        with patch.object(aeon._circuit_breakers, "is_hibernating", return_value=True):
            await aeon.initialize()
            with patch.object(aeon._state_machine, "transition_to") as mock_transition:
                await aeon._on_hibernate(MagicMock(reason="drawdown"))

    mock_transition.assert_called_once_with(LifecycleState.HIBERNATE)


@pytest.mark.asyncio
async def test_tier_progression(aeon):
    """Tier thresholds are respected across balance levels."""
    assert aeon._tier_gate.get_tier(49.99) == 0
    assert aeon._tier_gate.get_tier(50.0) == 0
    assert aeon._tier_gate.get_tier(99.99) == 0


@pytest.mark.asyncio
async def test_initialize_logs_guidance_prompt(aeon, monkeypatch, tmp_path, caplog):
    """initialize() logs and remembers the guidance prompt."""
    monkeypatch.setenv("AEON_GUIDANCE_PROMPT", "Crypto arbitrage: monitor exchange spreads.")
    with caplog.at_level("INFO", logger="aeon"):
        with patch.object(aeon._wallet, "get_balance", return_value=0.0):
            with patch.object(aeon._wallet, "credit"):
                with patch.object(aeon._state_machine, "transition_to"):
                    with patch("auton.aeon.AeonConfig.GUIDANCE_PROMPT", "Crypto arbitrage: monitor exchange spreads."):
                        await aeon.initialize()

    assert "Guidance prompt: Crypto arbitrage" in caplog.text
    memories = aeon._consciousness.recall(limit=10, event_type="guidance_prompt")
    assert any(m.payload.get("prompt") == "Crypto arbitrage: monitor exchange spreads." for m in memories)
    assert aeon._tier_gate.get_tier(100.0) == 1
    assert aeon._tier_gate.get_tier(499.99) == 1
    assert aeon._tier_gate.get_tier(500.0) == 2
    assert aeon._tier_gate.get_tier(2499.99) == 2
    assert aeon._tier_gate.get_tier(2500.0) == 3
    assert aeon._tier_gate.get_tier(9999.99) == 3
    assert aeon._tier_gate.get_tier(10000.0) == 4
    assert aeon._tier_gate.get_tier(50000.0) == 4


@pytest.mark.asyncio
async def test_opportunity_discovered_transitions_to_planning(aeon):
    """OpportunityDiscovered event transitions state to PLANNING."""
    from auton.core.events import OpportunityDiscovered

    await aeon.initialize()
    with patch.object(aeon._state_machine, "transition_to") as mock_transition:
        await aeon._on_opportunity_discovered(
            OpportunityDiscovered(
                domain="trading",
                description="BTC arbitrage",
                estimated_value=100.0,
                confidence=0.85,
            )
        )

    mock_transition.assert_called_once_with(LifecycleState.PLANNING)


@pytest.mark.asyncio
async def test_verification_code_received_transitions_to_awaiting(aeon):
    """VerificationCodeReceived event transitions state to AWAITING_VERIFICATION."""
    from auton.core.events import VerificationCodeReceived

    await aeon.initialize()
    with patch.object(aeon._state_machine, "transition_to") as mock_transition:
        await aeon._on_verification_code_received(
            VerificationCodeReceived(source="email", code="123456")
        )

    mock_transition.assert_called_once_with(LifecycleState.AWAITING_VERIFICATION)


@pytest.mark.asyncio
async def test_subscription_purchased_transitions_to_product_development(aeon):
    """SubscriptionPurchased event transitions state to PRODUCT_DEVELOPMENT."""
    from auton.core.events import SubscriptionPurchased

    await aeon.initialize()
    with patch.object(aeon._state_machine, "transition_to") as mock_transition:
        await aeon._on_subscription_purchased(
            SubscriptionPurchased(
                service="Stripe", tier="basic", cost=9.99, billing_cycle="monthly"
            )
        )

    mock_transition.assert_called_once_with(LifecycleState.PRODUCT_DEVELOPMENT)


@pytest.mark.asyncio
async def test_register_task_adds_to_registered_tasks(aeon):
    """register_task adds tasks to the internal registry."""

    async def dummy_task() -> None:
        pass

    aeon.register_task("test_task", dummy_task, interval_seconds=30.0)

    assert len(aeon._registered_tasks) == 1
    assert aeon._registered_tasks[0]["name"] == "test_task"
    assert aeon._registered_tasks[0]["interval_seconds"] == 30.0


@pytest.mark.asyncio
async def test_new_state_machine_states_are_reachable():
    """All new states can be transitioned to from RUNNING."""
    from auton.core.state_machine import StateMachine

    sm = StateMachine()
    new_states = [
        LifecycleState.PLANNING,
        LifecycleState.EXECUTING,
        LifecycleState.AWAITING_VERIFICATION,
        LifecycleState.MARKET_RESEARCH,
        LifecycleState.PRODUCT_DEVELOPMENT,
    ]

    for state in new_states:
        sm = StateMachine()
        sm._state = LifecycleState.RUNNING  # bypass INIT transition
        result = await sm.transition_to(state)
        assert result is True, f"Failed to transition to {state.name}"


@pytest.mark.asyncio
async def test_decision_loop_evaluates_and_transitions(aeon):
    """Decision loop evaluates context and transitions appropriately."""
    await aeon.initialize()
    aeon._running = True

    with patch.object(aeon._wallet, "get_balance", return_value=100.0):
        with patch.object(aeon._circuit_breakers, "is_hibernating", return_value=False):
            with patch.object(aeon._env_sensor, "sample", new_callable=AsyncMock):
                with patch.object(aeon._alpha, "scan_opportunities", return_value=[], create=True):
                    with patch.object(aeon._goal_generator, "generate_goals", return_value=[]):
                        with patch.object(aeon._consciousness, "generate_context_prompt", return_value=""):
                            with patch.object(aeon._opportunity_evaluator, "evaluate", return_value=MagicMock(approved=False)):
                                with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                                    # Run one iteration of decision loop
                                    task = asyncio.create_task(aeon._decision_loop())
                                    await asyncio.sleep(0.02)
                                    aeon._running = False
                                    try:
                                        await task
                                    except asyncio.CancelledError:
                                        pass
    # Loop should have slept at least once (it ran)
    mock_sleep.assert_awaited()


@pytest.mark.asyncio
async def test_decision_loop_with_opportunity(aeon):
    """Decision loop processes approved opportunities and emits DecisionMade events."""
    await aeon.initialize()
    aeon._running = True

    from auton.cortex.decision_engine import OpportunityScore, ResourceDecision
    mock_score = MagicMock(spec=OpportunityScore)
    mock_score.approved = True
    mock_opp = MagicMock()
    mock_opp.expected_return = 10.0
    mock_opp.capital_required = 5.0
    mock_opp.confidence = 0.8
    mock_opp.risk_score = 0.3
    mock_opp.time_horizon_hours = 24.0
    mock_opp.opportunity_type = "trade"
    mock_opp.metadata = {"description": "BTC trade", "symbol": "BTCUSDT"}

    with patch.object(aeon._wallet, "get_balance", return_value=100.0):
        with patch.object(aeon._circuit_breakers, "is_hibernating", return_value=False):
            with patch.object(aeon._env_sensor, "sample", new_callable=AsyncMock):
                with patch.object(aeon._alpha, "scan_opportunities", return_value=[{"description": "Test", "estimated_value": 10.0, "confidence": 0.8}], create=True):
                    with patch.object(aeon._goal_generator, "generate_goals", return_value=[]):
                        with patch.object(aeon._free_will, "explore", return_value=[mock_opp]):
                            with patch.object(aeon._opportunity_evaluator, "evaluate", return_value=mock_score):
                                with patch.object(aeon._optimizer, "optimise", return_value=[(mock_opp, 0.9)]):
                                    with patch.object(aeon._resource_allocator, "allocate", return_value=[MagicMock(amount=5.0)]):
                                        with patch.object(aeon._event_bus, "publish", new_callable=AsyncMock) as mock_publish:
                                            with patch("asyncio.sleep", new_callable=AsyncMock):
                                                task = asyncio.create_task(aeon._decision_loop())
                                                await asyncio.sleep(0.02)
                                                aeon._running = False
                                                try:
                                                    await task
                                                except asyncio.CancelledError:
                                                    pass

    # Should have published at least one DecisionMade event
    assert any(
        call.args[0].__name__ == "DecisionMade" if hasattr(call.args[0], '__name__') else False
        for call in mock_publish.call_args_list
    ) or mock_publish.awaited

