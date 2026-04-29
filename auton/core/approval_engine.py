"""Approval workflow engine for human-in-the-loop actions.

Manages the lifecycle of action proposals that require human approval
before execution. Proposals progress through:

    DRAFT → PENDING_APPROVAL → (APPROVED → EXECUTED)
                                      ↘ (REJECTED → CANCELLED)
                                      ↘ EXPIRED
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any

from auton.core.constants import APPROVAL_TTL_SECONDS
from auton.core.event_bus import EventBus
from auton.core.events import (
    ActionApproved,
    ActionExecuted,
    ActionExpired,
    ActionProposed,
    ActionRejected,
)

logger = logging.getLogger(__name__)


class ApprovalState(Enum):
    """Lifecycle states for an action proposal."""

    DRAFT = auto()
    PENDING_APPROVAL = auto()
    APPROVED = auto()
    REJECTED = auto()
    EXECUTED = auto()
    CANCELLED = auto()
    EXPIRED = auto()


_VALID_TRANSITIONS: dict[ApprovalState, set[ApprovalState]] = {
    ApprovalState.DRAFT: {ApprovalState.PENDING_APPROVAL},
    ApprovalState.PENDING_APPROVAL: {
        ApprovalState.APPROVED,
        ApprovalState.REJECTED,
        ApprovalState.EXPIRED,
    },
    ApprovalState.APPROVED: {ApprovalState.EXECUTED},
    ApprovalState.REJECTED: {ApprovalState.CANCELLED},
    ApprovalState.EXECUTED: set(),
    ApprovalState.CANCELLED: set(),
    ApprovalState.EXPIRED: set(),
}


@dataclass(frozen=True, slots=True)
class ProposalRequest:
    """Input for submitting a new action proposal."""

    action_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    risk_score: float = 0.0
    urgency: str = "normal"


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """Result returned by :meth:`ApprovalEngine.await_approval`."""

    proposal_id: str
    approved: bool
    status: str
    reason: str = ""


def _risk_level(score: float) -> str:
    """Map a numeric risk score to a categorical risk level."""
    if score < 0.3:
        return "low"
    if score < 0.7:
        return "medium"
    return "high"


class ApprovalEngine:
    """Async approval workflow engine with SQLite persistence and event-bus integration.

    Args:
        db_path: Path to the SQLite database. Defaults to ``data/approvals.db``.
        event_bus: Optional :class:`EventBus` instance used to publish state-transition
            events. If ``None``, events are silently dropped.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS proposals (
        uuid            TEXT PRIMARY KEY,
        action_type     TEXT NOT NULL,
        payload_json    TEXT NOT NULL,
        context_json    TEXT NOT NULL DEFAULT '{}',
        risk_score      REAL NOT NULL DEFAULT 0.0,
        urgency         TEXT NOT NULL DEFAULT 'normal',
        created_at      TEXT NOT NULL,
        expires_at      TEXT NOT NULL,
        status          TEXT NOT NULL,
        decided_at      TEXT,
        decision_reason TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
    CREATE INDEX IF NOT EXISTS idx_proposals_expires ON proposals(expires_at);
    """

    def __init__(
        self,
        db_path: str | Path = "data/approvals.db",
        event_bus: EventBus | None = None,
        expiration_interval: float = 30.0,
    ) -> None:
        self._db_path = str(db_path)
        self._event_bus = event_bus
        self._expiration_interval = expiration_interval
        self._local = threading.local()
        self._ensure_schema()
        self._waiters: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        self._expiration_task: asyncio.Task[Any] | None = None
        self._shutdown = False

    # ------------------------------------------------------------------ #
    # Connection management (one connection per thread)
    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(self._DDL)
            conn.commit()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def submit_proposal(self, action: ProposalRequest) -> str:
        """Submit a new action proposal and move it to ``PENDING_APPROVAL``.

        Args:
            action: The proposal request payload.

        Returns:
            The generated proposal UUID.
        """
        proposal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=APPROVAL_TTL_SECONDS)

        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO proposals
                (uuid, action_type, payload_json, context_json, risk_score, urgency,
                 created_at, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    action.action_type,
                    json.dumps(action.payload),
                    json.dumps(action.context),
                    action.risk_score,
                    action.urgency,
                    now.isoformat(),
                    expires_at.isoformat(),
                    ApprovalState.DRAFT.name,
                ),
            )
            conn.commit()

        await self._transition(proposal_id, ApprovalState.PENDING_APPROVAL)
        return proposal_id

    async def await_approval(
        self, proposal_id: str, timeout: float | None = None
    ) -> ApprovalResult:
        """Block until the proposal reaches a terminal state.

        Terminal states considered are ``APPROVED``, ``REJECTED``, and ``EXPIRED``.

        Args:
            proposal_id: The proposal UUID to wait on.
            timeout: Maximum seconds to wait. ``None`` blocks indefinitely.

        Returns:
            An :class:`ApprovalResult` describing the outcome.
        """
        async with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT status, decision_reason FROM proposals WHERE uuid = ?",
                    (proposal_id,),
                )
                .fetchone()
            )
            if not row:
                return ApprovalResult(
                    proposal_id=proposal_id,
                    approved=False,
                    status="NOT_FOUND",
                    reason="Proposal not found",
                )

            status, reason = row
            if status in {
                ApprovalState.APPROVED.name,
                ApprovalState.REJECTED.name,
                ApprovalState.EXPIRED.name,
            }:
                return ApprovalResult(
                    proposal_id=proposal_id,
                    approved=status == ApprovalState.APPROVED.name,
                    status=status,
                    reason=reason or "",
                )

            if proposal_id not in self._waiters:
                self._waiters[proposal_id] = asyncio.Event()
            event = self._waiters[proposal_id]

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return ApprovalResult(
                proposal_id=proposal_id,
                approved=False,
                status="TIMEOUT",
                reason="Wait timed out",
            )

        # Re-check state after wakeup
        async with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT status, decision_reason FROM proposals WHERE uuid = ?",
                    (proposal_id,),
                )
                .fetchone()
            )
            if not row:
                return ApprovalResult(
                    proposal_id=proposal_id,
                    approved=False,
                    status="NOT_FOUND",
                    reason="Proposal not found",
                )
            status, reason = row
            return ApprovalResult(
                proposal_id=proposal_id,
                approved=status == ApprovalState.APPROVED.name,
                status=status,
                reason=reason or "",
            )

    async def approve(self, proposal_id: str, reason: str = "") -> bool:
        """Approve a pending proposal.

        Args:
            proposal_id: The proposal UUID.
            reason: Optional decision reason.

        Returns:
            True if the transition succeeded.
        """
        await self._set_reason(proposal_id, reason)
        return await self._transition(proposal_id, ApprovalState.APPROVED)

    async def reject(self, proposal_id: str, reason: str = "") -> bool:
        """Reject a pending proposal.

        Args:
            proposal_id: The proposal UUID.
            reason: Optional decision reason.

        Returns:
            True if the transition succeeded.
        """
        await self._set_reason(proposal_id, reason)
        return await self._transition(proposal_id, ApprovalState.REJECTED)

    async def execute(self, proposal_id: str) -> bool:
        """Mark an approved proposal as executed.

        Args:
            proposal_id: The proposal UUID.

        Returns:
            True if the transition succeeded.
        """
        return await self._transition(proposal_id, ApprovalState.EXECUTED)

    async def cancel(self, proposal_id: str, reason: str = "") -> bool:
        """Cancel a rejected proposal.

        Args:
            proposal_id: The proposal UUID.
            reason: Optional cancellation reason.

        Returns:
            True if the transition succeeded.
        """
        await self._set_reason(proposal_id, reason)
        return await self._transition(proposal_id, ApprovalState.CANCELLED)

    async def expire(self, proposal_id: str) -> bool:
        """Expire a pending proposal.

        Args:
            proposal_id: The proposal UUID.

        Returns:
            True if the transition succeeded.
        """
        return await self._transition(proposal_id, ApprovalState.EXPIRED)

    async def start(self) -> None:
        """Start the background expiration watcher."""
        if self._expiration_task is None or self._expiration_task.done():
            self._expiration_task = asyncio.create_task(self._run_expiration_loop())

    async def stop(self) -> None:
        """Stop the background expiration watcher and wake any waiters."""
        self._shutdown = True
        if self._expiration_task and not self._expiration_task.done():
            self._expiration_task.cancel()
            try:
                await self._expiration_task
            except asyncio.CancelledError:
                pass

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """Fetch a proposal by UUID.

        Returns:
            A dict representing the proposal, or ``None`` if not found.
        """
        row = (
            self._conn()
            .execute(
                """
                SELECT uuid, action_type, payload_json, context_json, risk_score,
                       urgency, created_at, expires_at, status, decided_at,
                       decision_reason
                FROM proposals WHERE uuid = ?
                """,
                (proposal_id,),
            )
            .fetchone()
        )
        if not row:
            return None
        return {
            "uuid": row[0],
            "action_type": row[1],
            "payload": json.loads(row[2]),
            "context": json.loads(row[3]),
            "risk_score": row[4],
            "urgency": row[5],
            "created_at": row[6],
            "expires_at": row[7],
            "status": row[8],
            "decided_at": row[9],
            "decision_reason": row[10],
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    async def _set_reason(self, proposal_id: str, reason: str) -> None:
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "UPDATE proposals SET decision_reason = ? WHERE uuid = ?",
                    (reason, proposal_id),
                )
                conn.commit()

    async def _transition(self, proposal_id: str, new_state: ApprovalState) -> bool:
        """Attempt a state transition, persisting and emitting an event on success."""
        async with self._lock:
            row = (
                self._conn()
                .execute(
                    "SELECT status FROM proposals WHERE uuid = ?", (proposal_id,)
                )
                .fetchone()
            )
            if not row:
                logger.warning(
                    "ApprovalEngine: proposal %s not found", proposal_id
                )
                return False

            old_state = ApprovalState[row[0]]
            if new_state not in _VALID_TRANSITIONS.get(old_state, set()):
                logger.warning(
                    "ApprovalEngine: invalid transition %s -> %s for %s",
                    old_state.name,
                    new_state.name,
                    proposal_id,
                )
                return False

            decided_at = None
            if new_state in {
                ApprovalState.APPROVED,
                ApprovalState.REJECTED,
                ApprovalState.EXPIRED,
            }:
                decided_at = datetime.now(timezone.utc).isoformat()

            with self._conn() as conn:
                if decided_at:
                    conn.execute(
                        """
                        UPDATE proposals
                        SET status = ?, decided_at = ?
                        WHERE uuid = ?
                        """,
                        (new_state.name, decided_at, proposal_id),
                    )
                else:
                    conn.execute(
                        "UPDATE proposals SET status = ? WHERE uuid = ?",
                        (new_state.name, proposal_id),
                    )
                conn.commit()

            event = self._waiters.get(proposal_id)

        # Emit and signal outside the lock to avoid blocking other operations
        await self._emit_state_event(proposal_id, old_state, new_state)
        if event:
            event.set()

        return True

    async def _emit_state_event(
        self,
        proposal_id: str,
        old_state: ApprovalState,
        new_state: ApprovalState,
    ) -> None:
        if self._event_bus is None:
            return

        row = (
            self._conn()
            .execute(
                """
                SELECT action_type, payload_json, context_json, risk_score,
                       urgency, created_at, expires_at, decision_reason
                FROM proposals WHERE uuid = ?
                """,
                (proposal_id,),
            )
            .fetchone()
        )
        if not row:
            return

        action_type, payload_json, _context_json, risk_score, urgency, created_at, expires_at, decision_reason = row
        payload = json.loads(payload_json)
        created_dt = datetime.fromisoformat(created_at)
        expires_dt = datetime.fromisoformat(expires_at) if expires_at else None
        now = datetime.now(timezone.utc)

        try:
            if new_state == ApprovalState.PENDING_APPROVAL:
                event = ActionProposed(
                    proposal_id=proposal_id,
                    action_type=action_type,
                    payload=payload,
                    risk_level=_risk_level(risk_score),
                    requested_at=created_dt,
                    expires_at=expires_dt,
                )
                await self._event_bus.publish(ActionProposed, event)
            elif new_state == ApprovalState.APPROVED:
                event = ActionApproved(
                    proposal_id=proposal_id,
                    approver="human",
                    approved_at=now,
                )
                await self._event_bus.publish(ActionApproved, event)
            elif new_state == ApprovalState.REJECTED:
                event = ActionRejected(
                    proposal_id=proposal_id,
                    approver="human",
                    reason=decision_reason or "",
                    rejected_at=now,
                )
                await self._event_bus.publish(ActionRejected, event)
            elif new_state == ApprovalState.EXECUTED:
                event = ActionExecuted(
                    proposal_id=proposal_id,
                    action_type=action_type,
                    payload=payload,
                    executed_at=now,
                )
                await self._event_bus.publish(ActionExecuted, event)
            elif new_state == ApprovalState.EXPIRED:
                event = ActionExpired(
                    proposal_id=proposal_id,
                    action_type=action_type,
                    expired_at=now,
                )
                await self._event_bus.publish(ActionExpired, event)
        except Exception:
            logger.exception(
                "ApprovalEngine: failed to emit event for %s", proposal_id
            )

    async def _run_expiration_loop(self) -> None:
        while not self._shutdown:
            try:
                await asyncio.sleep(self._expiration_interval)
                now = datetime.now(timezone.utc).isoformat()
                conn = self._conn()
                cursor = conn.execute(
                    """
                    SELECT uuid FROM proposals
                    WHERE status = ? AND expires_at < ?
                    """,
                    (ApprovalState.PENDING_APPROVAL.name, now),
                )
                expired_ids = [row[0] for row in cursor.fetchall()]
                for pid in expired_ids:
                    await self.expire(pid)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("ApprovalEngine: expiration loop error")
