"""Tests for the approval workflow engine."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from auton.core.approval_engine import (
    ApprovalEngine,
    ApprovalResult,
    ApprovalState,
    ProposalRequest,
)
from auton.core.event_bus import EventBus
from auton.core.events import (
    ActionApproved,
    ActionExecuted,
    ActionExpired,
    ActionProposed,
    ActionRejected,
)


@pytest.fixture
def tmp_db() -> str:
    with tempfile.TemporaryDirectory() as td:
        yield str(Path(td) / "approvals.db")


@pytest.fixture
def engine(tmp_db: str) -> ApprovalEngine:
    return ApprovalEngine(db_path=tmp_db)


@pytest.fixture
def bus_and_engine(tmp_db: str) -> tuple[EventBus, ApprovalEngine]:
    bus = EventBus()
    eng = ApprovalEngine(db_path=tmp_db, event_bus=bus)
    return bus, eng


# ---------------------------------------------------------------------------
# Submission and basic retrieval
# ---------------------------------------------------------------------------


class TestSubmitProposal:
    @pytest.mark.asyncio
    async def test_creates_proposal_with_uuid(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade", payload={"symbol": "BTC"})
        pid = await engine.submit_proposal(req)
        assert isinstance(pid, str)
        assert len(pid) == 36

    @pytest.mark.asyncio
    async def test_stores_all_fields(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(
            action_type="deploy",
            payload={"env": "prod"},
            context={"branch": "main"},
            risk_score=0.8,
            urgency="high",
        )
        pid = await engine.submit_proposal(req)
        prop = engine.get_proposal(pid)
        assert prop is not None
        assert prop["action_type"] == "deploy"
        assert prop["payload"] == {"env": "prod"}
        assert prop["context"] == {"branch": "main"}
        assert prop["risk_score"] == 0.8
        assert prop["urgency"] == "high"
        assert prop["status"] == ApprovalState.PENDING_APPROVAL.name

    @pytest.mark.asyncio
    async def test_sets_expires_at(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="spend")
        pid = await engine.submit_proposal(req)
        prop = engine.get_proposal(pid)
        assert prop is not None
        created = datetime.fromisoformat(prop["created_at"])
        expires = datetime.fromisoformat(prop["expires_at"])
        assert expires > created


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestTransitions:
    @pytest.mark.asyncio
    async def test_valid_draft_to_pending(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)
        prop = engine.get_proposal(pid)
        assert prop is not None
        assert prop["status"] == ApprovalState.PENDING_APPROVAL.name

    @pytest.mark.asyncio
    async def test_approve_then_execute(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)
        assert await engine.approve(pid, reason="looks good")
        prop = engine.get_proposal(pid)
        assert prop is not None
        assert prop["status"] == ApprovalState.APPROVED.name
        assert prop["decision_reason"] == "looks good"
        assert prop["decided_at"] is not None
        assert await engine.execute(pid)
        prop = engine.get_proposal(pid)
        assert prop["status"] == ApprovalState.EXECUTED.name

    @pytest.mark.asyncio
    async def test_reject_then_cancel(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="deploy")
        pid = await engine.submit_proposal(req)
        assert await engine.reject(pid, reason="too risky")
        prop = engine.get_proposal(pid)
        assert prop is not None
        assert prop["status"] == ApprovalState.REJECTED.name
        assert prop["decision_reason"] == "too risky"
        assert await engine.cancel(pid)
        prop = engine.get_proposal(pid)
        assert prop["status"] == ApprovalState.CANCELLED.name

    @pytest.mark.asyncio
    async def test_expire_pending(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="spend")
        pid = await engine.submit_proposal(req)
        assert await engine.expire(pid)
        prop = engine.get_proposal(pid)
        assert prop is not None
        assert prop["status"] == ApprovalState.EXPIRED.name
        assert prop["decided_at"] is not None

    @pytest.mark.asyncio
    async def test_invalid_transition_approved_to_rejected(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)
        assert await engine.approve(pid)
        assert not await engine.reject(pid)

    @pytest.mark.asyncio
    async def test_invalid_transition_executed_to_anything(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)
        assert await engine.approve(pid)
        assert await engine.execute(pid)
        assert not await engine.expire(pid)
        assert not await engine.cancel(pid)


# ---------------------------------------------------------------------------
# await_approval
# ---------------------------------------------------------------------------


class TestAwaitApproval:
    @pytest.mark.asyncio
    async def test_returns_on_approve(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)

        async def approver() -> None:
            await asyncio.sleep(0.05)
            await engine.approve(pid)

        asyncio.create_task(approver())
        result = await engine.await_approval(pid)
        assert result.proposal_id == pid
        assert result.approved is True
        assert result.status == ApprovalState.APPROVED.name

    @pytest.mark.asyncio
    async def test_returns_on_reject(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)

        async def rejecter() -> None:
            await asyncio.sleep(0.05)
            await engine.reject(pid, reason="denied")

        asyncio.create_task(rejecter())
        result = await engine.await_approval(pid)
        assert result.approved is False
        assert result.status == ApprovalState.REJECTED.name
        assert result.reason == "denied"

    @pytest.mark.asyncio
    async def test_returns_on_expire(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)

        async def expirer() -> None:
            await asyncio.sleep(0.05)
            await engine.expire(pid)

        asyncio.create_task(expirer())
        result = await engine.await_approval(pid)
        assert result.approved is False
        assert result.status == ApprovalState.EXPIRED.name

    @pytest.mark.asyncio
    async def test_times_out(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)
        result = await engine.await_approval(pid, timeout=0.05)
        assert result.status == "TIMEOUT"
        assert result.approved is False

    @pytest.mark.asyncio
    async def test_already_terminal_returns_immediately(self, engine: ApprovalEngine) -> None:
        req = ProposalRequest(action_type="trade")
        pid = await engine.submit_proposal(req)
        await engine.approve(pid)
        result = await engine.await_approval(pid)
        assert result.status == ApprovalState.APPROVED.name


# ---------------------------------------------------------------------------
# Event bus integration
# ---------------------------------------------------------------------------


class TestEventBusIntegration:
    @pytest.mark.asyncio
    async def test_proposed_event_fired(self, bus_and_engine: tuple[EventBus, ApprovalEngine]) -> None:
        bus, eng = bus_and_engine
        received: list[Any] = []

        async def handler(event: Any) -> None:
            received.append(event)

        await bus.subscribe(ActionProposed, handler)
        req = ProposalRequest(action_type="trade", payload={"sym": "ETH"}, risk_score=0.9)
        await eng.submit_proposal(req)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert isinstance(received[0], ActionProposed)
        assert received[0].action_type == "trade"
        assert received[0].risk_level == "high"
        assert received[0].payload == {"sym": "ETH"}

    @pytest.mark.asyncio
    async def test_approved_event_fired(self, bus_and_engine: tuple[EventBus, ApprovalEngine]) -> None:
        bus, eng = bus_and_engine
        received: list[Any] = []

        async def handler(event: Any) -> None:
            received.append(event)

        await bus.subscribe(ActionApproved, handler)
        pid = await eng.submit_proposal(ProposalRequest(action_type="trade"))
        await eng.approve(pid)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert isinstance(received[0], ActionApproved)
        assert received[0].proposal_id == pid

    @pytest.mark.asyncio
    async def test_rejected_event_fired(self, bus_and_engine: tuple[EventBus, ApprovalEngine]) -> None:
        bus, eng = bus_and_engine
        received: list[Any] = []

        async def handler(event: Any) -> None:
            received.append(event)

        await bus.subscribe(ActionRejected, handler)
        pid = await eng.submit_proposal(ProposalRequest(action_type="trade"))
        await eng.reject(pid, reason="no")
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert isinstance(received[0], ActionRejected)
        assert received[0].reason == "no"

    @pytest.mark.asyncio
    async def test_executed_event_fired(self, bus_and_engine: tuple[EventBus, ApprovalEngine]) -> None:
        bus, eng = bus_and_engine
        received: list[Any] = []

        async def handler(event: Any) -> None:
            received.append(event)

        await bus.subscribe(ActionExecuted, handler)
        pid = await eng.submit_proposal(ProposalRequest(action_type="deploy", payload={"v": 1}))
        await eng.approve(pid)
        await eng.execute(pid)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert isinstance(received[0], ActionExecuted)
        assert received[0].payload == {"v": 1}

    @pytest.mark.asyncio
    async def test_expired_event_fired(self, bus_and_engine: tuple[EventBus, ApprovalEngine]) -> None:
        bus, eng = bus_and_engine
        received: list[Any] = []

        async def handler(event: Any) -> None:
            received.append(event)

        await bus.subscribe(ActionExpired, handler)
        pid = await eng.submit_proposal(ProposalRequest(action_type="trade"))
        await eng.expire(pid)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert isinstance(received[0], ActionExpired)


# ---------------------------------------------------------------------------
# Auto-expiration
# ---------------------------------------------------------------------------


class TestAutoExpiration:
    @pytest.mark.asyncio
    async def test_expiration_loop_expires_proposals(self, tmp_db: str) -> None:
        eng = ApprovalEngine(db_path=tmp_db, expiration_interval=0.1)
        # Patch expires_at to be in the past so the loop catches it
        req = ProposalRequest(action_type="trade")
        pid = await eng.submit_proposal(req)
        # Manually backdate expires_at
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        conn = eng._conn()
        conn.execute("UPDATE proposals SET expires_at = ? WHERE uuid = ?", (past, pid))
        conn.commit()

        await eng.start()
        await asyncio.sleep(0.3)
        await eng.stop()

        prop = eng.get_proposal(pid)
        assert prop is not None
        assert prop["status"] == ApprovalState.EXPIRED.name

    @pytest.mark.asyncio
    async def test_expiration_loop_ignores_future_proposals(self, tmp_db: str) -> None:
        eng = ApprovalEngine(db_path=tmp_db, expiration_interval=0.1)
        req = ProposalRequest(action_type="trade")
        pid = await eng.submit_proposal(req)

        await eng.start()
        await asyncio.sleep(0.3)
        await eng.stop()

        prop = eng.get_proposal(pid)
        assert prop is not None
        assert prop["status"] == ApprovalState.PENDING_APPROVAL.name


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, tmp_db: str) -> None:
        eng = ApprovalEngine(db_path=tmp_db)
        conn = eng._conn()
        journal = conn.execute("PRAGMA journal_mode").fetchone()
        assert journal is not None
        assert journal[0].lower() == "wal"

    @pytest.mark.asyncio
    async def test_survives_reopen(self, tmp_db: str) -> None:
        eng = ApprovalEngine(db_path=tmp_db)
        pid = await eng.submit_proposal(ProposalRequest(action_type="trade", payload={"a": 1}))
        await eng.approve(pid)

        eng2 = ApprovalEngine(db_path=tmp_db)
        prop = eng2.get_proposal(pid)
        assert prop is not None
        assert prop["status"] == ApprovalState.APPROVED.name
        assert prop["payload"] == {"a": 1}
