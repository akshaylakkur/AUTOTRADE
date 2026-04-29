"""Pytest suite for the HumanGateway interception layer."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from auton.limbs.human_gateway import (
    ActionExecuted,
    ActionProposed,
    ActionProposal,
    ActionRejected,
    ApprovalStatus,
    HumanGateway,
    HumanGatewayError,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_executor():
    """A simple async executor with the methods the gateway knows how to wrap."""
    exe = AsyncMock()
    exe.name = "MockExecutor"
    exe.place_order = AsyncMock(return_value={"order_id": "123", "status": "FILLED"})
    exe.deploy = AsyncMock(return_value={"deployment_id": "dep-1", "url": "https://example.com"})
    exe.create_payment_intent = AsyncMock(return_value={"intent_id": "pi_1", "status": "created"})
    exe.provision_resource = AsyncMock(return_value={"resource_id": "res-1"})
    return exe


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock()
    bus.publish = AsyncMock()
    bus.emit = AsyncMock()
    return bus


@pytest.fixture
def mock_wallet():
    w = AsyncMock()
    w.get_balance = AsyncMock(return_value=50.0)
    return w


@pytest.fixture
def unrestricted_gateway(mock_executor, mock_event_bus):
    return HumanGateway(
        executor=mock_executor,
        event_bus=mock_event_bus,
        restricted_mode=False,
    )


@pytest.fixture
def restricted_gateway(mock_executor, mock_event_bus):
    return HumanGateway(
        executor=mock_executor,
        event_bus=mock_event_bus,
        restricted_mode=True,
        approval_timeout_seconds=0.5,
        default_recipient="human@example.com",
    )


# --------------------------------------------------------------------------- #
# Pass-through (unrestricted) behaviour
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unrestricted_execute_trade(mock_executor, unrestricted_gateway):
    result = await unrestricted_gateway.execute_trade(symbol="BTCUSDT", side="BUY", quantity=0.1)
    mock_executor.place_order.assert_awaited_once_with(symbol="BTCUSDT", side="BUY", quantity=0.1)
    assert result["order_id"] == "123"


@pytest.mark.asyncio
async def test_unrestricted_deploy_product(mock_executor, unrestricted_gateway):
    result = await unrestricted_gateway.deploy_product(product_id="prod-1")
    mock_executor.deploy.assert_awaited_once_with(product_id="prod-1")
    assert result["deployment_id"] == "dep-1"


@pytest.mark.asyncio
async def test_unrestricted_spend_funds(mock_executor, unrestricted_gateway):
    result = await unrestricted_gateway.spend_funds(amount=500, currency="usd")
    mock_executor.create_payment_intent.assert_awaited_once_with(amount=500, currency="usd")
    assert result["intent_id"] == "pi_1"


@pytest.mark.asyncio
async def test_unrestricted_provision_resource(mock_executor, unrestricted_gateway):
    result = await unrestricted_gateway.provision_resource(instance_type="t2.micro")
    mock_executor.provision_resource.assert_awaited_once_with(instance_type="t2.micro")
    assert result["resource_id"] == "res-1"


# --------------------------------------------------------------------------- #
# Restricted mode — proposal creation and approval flow
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_restricted_execute_trade_creates_proposal(mock_executor, restricted_gateway, mock_event_bus):
    task = asyncio.create_task(restricted_gateway.execute_trade(symbol="BTCUSDT", side="BUY", quantity=0.1))
    # Wait briefly for the proposal to be created
    await asyncio.sleep(0.05)

    # Event bus should have received a proposal event
    mock_event_bus.emit.assert_awaited()
    calls = mock_event_bus.emit.await_args_list
    assert any("human_gateway.proposal.pending" in str(c.args[0]) for c in calls)

    # Approve
    proposal_id = None
    for c in calls:
        if "human_gateway.proposal.pending" in str(c.args[0]):
            proposal_id = c.args[1]["proposal_id"]
            break
    assert proposal_id is not None

    restricted_gateway.approve(proposal_id)
    result = await task
    assert result["order_id"] == "123"


@pytest.mark.asyncio
async def test_restricted_deploy_product_rejected(mock_executor, restricted_gateway, mock_event_bus):
    task = asyncio.create_task(restricted_gateway.deploy_product(product_id="prod-1"))
    await asyncio.sleep(0.05)

    calls = mock_event_bus.emit.await_args_list
    proposal_id = None
    for c in calls:
        if "human_gateway.proposal.pending" in str(c.args[0]):
            proposal_id = c.args[1]["proposal_id"]
            break
    assert proposal_id is not None

    restricted_gateway.reject(proposal_id)
    with pytest.raises(HumanGatewayError, match="rejected by human"):
        await task


@pytest.mark.asyncio
async def test_restricted_times_out(mock_executor, restricted_gateway):
    with pytest.raises(HumanGatewayError, match="timed out"):
        await restricted_gateway.spend_funds(amount=100, currency="usd")


# --------------------------------------------------------------------------- #
# Proposal content
# --------------------------------------------------------------------------- #


def test_build_proposal_structure(restricted_gateway):
    payload = {"symbol": "ETHUSDT", "side": "SELL", "quantity": 1.0}
    proposal = restricted_gateway._build_proposal("execute_trade", payload)
    assert isinstance(proposal, ActionProposal)
    assert proposal.action_type == "execute_trade"
    assert proposal.action_payload == payload
    assert proposal.proposal_id.startswith("PROP-")
    assert proposal.status == ApprovalStatus.PENDING
    assert 0.0 <= proposal.risk_score <= 1.0
    assert "utc_time" in proposal.environmental_context


# --------------------------------------------------------------------------- #
# Approval API edge cases
# --------------------------------------------------------------------------- #


def test_approve_unknown_proposal(restricted_gateway):
    assert restricted_gateway.approve("UNKNOWN-999") is False


def test_reject_unknown_proposal(restricted_gateway):
    assert restricted_gateway.reject("UNKNOWN-999") is False


def test_get_proposal_status(restricted_gateway):
    # Simulate a pending proposal by injecting state manually
    event = asyncio.Event()
    restricted_gateway._pending["TEST-1"] = event
    restricted_gateway._approvals["TEST-1"] = ApprovalStatus.PENDING
    assert restricted_gateway.get_proposal_status("TEST-1") == ApprovalStatus.PENDING

    restricted_gateway.approve("TEST-1")
    assert restricted_gateway.get_proposal_status("TEST-1") == ApprovalStatus.APPROVED
    assert restricted_gateway.approve("TEST-1") is False  # already resolved


# --------------------------------------------------------------------------- #
# Event emission (typed + string)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_typed_event_bus_publish(mock_executor):
    typed_bus = AsyncMock()
    typed_bus.publish = AsyncMock()
    gateway = HumanGateway(
        executor=mock_executor,
        event_bus=typed_bus,
        restricted_mode=True,
        approval_timeout_seconds=0.5,
    )
    task = asyncio.create_task(gateway.execute_trade(symbol="BTCUSDT", side="BUY", quantity=0.1))
    await asyncio.sleep(0.05)

    # Extract proposal_id from the typed publish call
    proposal_id = None
    for call in typed_bus.publish.await_args_list:
        event_type, payload = call.args
        if event_type is ActionProposed:
            proposal_id = payload.proposal.proposal_id
            break
    assert proposal_id is not None

    gateway.approve(proposal_id)
    await task

    # Should have ActionExecuted typed event
    event_types = [c.args[0] for c in typed_bus.publish.await_args_list]
    assert ActionExecuted in event_types


@pytest.mark.asyncio
async def test_rejected_emits_typed_event(mock_executor):
    typed_bus = AsyncMock()
    typed_bus.publish = AsyncMock()
    gateway = HumanGateway(
        executor=mock_executor,
        event_bus=typed_bus,
        restricted_mode=True,
        approval_timeout_seconds=0.5,
    )
    task = asyncio.create_task(gateway.deploy_product(product_id="p1"))
    await asyncio.sleep(0.05)

    calls = typed_bus.publish.await_args_list
    proposal_id = None
    for call in calls:
        event_type, payload = call.args
        if event_type is ActionProposed:
            proposal_id = payload.proposal.proposal_id
            break
    assert proposal_id is not None

    gateway.reject(proposal_id)
    with pytest.raises(HumanGatewayError):
        await task

    event_types = [c.args[0] for c in typed_bus.publish.await_args_list]
    assert ActionRejected in event_types


# --------------------------------------------------------------------------- #
# Reasoning and market data callbacks
# --------------------------------------------------------------------------- #


def test_reasoning_callback(mock_executor):
    gateway = HumanGateway(
        executor=mock_executor,
        restricted_mode=False,
        reasoning_callback=lambda action, payload: f"Custom: {action}",
    )
    proposal = gateway._build_proposal("execute_trade", {})
    assert proposal.reasoning_summary == "Custom: execute_trade"


def test_market_data_callback(mock_executor):
    gateway = HumanGateway(
        executor=mock_executor,
        restricted_mode=False,
        market_data_callback=lambda: {"btc": 42000.0},
    )
    proposal = gateway._build_proposal("execute_trade", {})
    assert proposal.market_snapshot == {"btc": 42000.0}


# --------------------------------------------------------------------------- #
# Fallback forwarding (generic execute)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_forward_via_generic_execute():
    """If the executor only has ``execute``, the gateway should fall back to it."""

    class GenericExecutor:
        name = "GenericExecutor"

        async def execute(self, action):
            return {"generic": True, "action": action}

    executor = GenericExecutor()
    gateway = HumanGateway(executor=executor, restricted_mode=False)

    result = await gateway.execute_trade(symbol="X", side="BUY", quantity=1)
    assert result["generic"] is True
    assert result["action"] == {"method": "place_order", "kwargs": {"symbol": "X", "side": "BUY", "quantity": 1}}


# --------------------------------------------------------------------------- #
# Environmental context
# --------------------------------------------------------------------------- #


def test_infer_market_hours():
    from datetime import datetime, timezone

    # US market open (14:00 UTC ~ 9am ET)
    us_open = datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc)
    assert HumanGateway._infer_market_hours(us_open) == "US_MARKET_OPEN"

    # EU market open (10:00 UTC ~ 11am CET)
    eu_open = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
    assert HumanGateway._infer_market_hours(eu_open) == "EU_MARKET_OPEN"

    # After hours
    after = datetime(2024, 1, 15, 22, 0, tzinfo=timezone.utc)
    assert HumanGateway._infer_market_hours(after) == "AFTER_HOURS"


# --------------------------------------------------------------------------- #
# Proxy / attribute access
# --------------------------------------------------------------------------- #


def test_gateway_name(mock_executor):
    gateway = HumanGateway(executor=mock_executor)
    assert gateway.name == "MockExecutor"


def test_gateway_attribute_proxy(mock_executor):
    gateway = HumanGateway(executor=mock_executor)
    assert gateway.place_order is mock_executor.place_order
