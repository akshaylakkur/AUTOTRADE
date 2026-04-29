"""Risk coordinator for Project ÆON — reviews high-stakes decisions before execution.

The risk coordinator acts as a final gatekeeper for any action that could
materially impact ÆON's balance, reputation, or survival.  It supports
multi-tier risk classification, simulated multi-signature approval, and an
emergency kill switch that can pause all spending and revoke vault keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any

from .exceptions import (
    ApprovalRequired,
    EmergencyPauseActive,
    PolicyViolation,
    ThreatDetected,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class RiskLevel(Enum):
    """Risk classification for a pending decision."""

    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    CRITICAL = auto()


@dataclass(frozen=True, slots=True)
class RiskReview:
    """Outcome of reviewing a single decision."""

    decision_id: str
    risk_level: RiskLevel
    approved: bool
    required_approvals: int
    current_approvals: int
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """A decision awaiting human or automated approval."""

    decision_id: str
    action: str
    amount: float
    risk_score: float
    confidence: float
    strategy: str
    requested_at: datetime
    required_approvals: int
    approvals: list[tuple[str, datetime]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return len(self.approvals) >= self.required_approvals


# ---------------------------------------------------------------------------
# RiskCoordinator
# ---------------------------------------------------------------------------

class RiskCoordinator:
    """Reviews high-stakes decisions and enforces multi-sig style approvals.

    The coordinator classifies every decision into one of four risk levels:

    * **LOW** — auto-approved (e.g. small trades within tier limits).
    * **MEDIUM** — requires one approval (e.g. trades near position caps).
    * **HIGH** — requires two approvals (e.g. SaaS launches, contractor hires).
    * **CRITICAL** — blocked until explicitly approved by multiple parties and
      may trigger an emergency pause if the risk score is extreme.

    Integration points:
    * :class:`AuditLog` — every review and approval is logged.
    * :class:`SpendGuard` — budget checks are enforced.
    * :class:`SecureExecutionEnvironment` — emergency kill switch.
    """

    _TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS risk_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_id TEXT UNIQUE NOT NULL,
        action TEXT NOT NULL,
        amount REAL NOT NULL DEFAULT 0.0,
        risk_score REAL NOT NULL DEFAULT 0.0,
        confidence REAL NOT NULL DEFAULT 0.0,
        strategy TEXT NOT NULL DEFAULT '',
        risk_level TEXT NOT NULL DEFAULT 'LOW',
        required_approvals INTEGER NOT NULL DEFAULT 0,
        approval_count INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        requested_at TEXT NOT NULL,
        resolved_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_risk_decision ON risk_reviews(decision_id);
    CREATE INDEX IF NOT EXISTS idx_risk_status ON risk_reviews(status);
    """

    _APPROVAL_SQL = """
    CREATE TABLE IF NOT EXISTS risk_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_id TEXT NOT NULL,
        approver_id TEXT NOT NULL,
        approved_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_approval_decision ON risk_approvals(decision_id);
    """

    def __init__(
        self,
        *,
        audit_log=None,
        spend_guard=None,
        execution_env=None,
        db_path: str = "data/aeon_risk.db",
        # Risk thresholds (per tier)
        low_threshold: float = 0.30,
        medium_threshold: float = 0.55,
        high_threshold: float = 0.75,
        critical_threshold: float = 0.90,
        # Amount thresholds (absolute USD)
        low_amount: float = 1.0,
        medium_amount: float = 10.0,
        high_amount: float = 50.0,
        critical_amount: float = 200.0,
        # Multi-sig requirements per risk level
        medium_approvals: int = 1,
        high_approvals: int = 2,
        critical_approvals: int = 3,
        # Auto-reject if risk score exceeds this regardless of amount
        absolute_risk_limit: float = 0.95,
        # Max age for pending decisions (hours)
        pending_ttl_hours: float = 24.0,
    ) -> None:
        self._audit_log = audit_log
        self._spend_guard = spend_guard
        self._execution_env = execution_env
        self._db_path = db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._low_threshold = low_threshold
        self._medium_threshold = medium_threshold
        self._high_threshold = high_threshold
        self._critical_threshold = critical_threshold

        self._low_amount = low_amount
        self._medium_amount = medium_amount
        self._high_amount = high_amount
        self._critical_amount = critical_amount

        self._medium_approvals = medium_approvals
        self._high_approvals = high_approvals
        self._critical_approvals = critical_approvals

        self._absolute_risk_limit = absolute_risk_limit
        self._pending_ttl_hours = pending_ttl_hours

        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(self._TABLE_SQL)
            conn.executescript(self._APPROVAL_SQL)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _hash_decision(decision: dict[str, Any]) -> str:
        """Create a stable decision_id from the decision payload."""
        canonical = json.dumps(decision, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def review_decision(
        self,
        decision: dict[str, Any],
        *,
        balance: float = 0.0,
    ) -> RiskReview:
        """Classify a decision and determine whether it may proceed.

        Args:
            decision: Dict expected to contain at least ``action``,
                ``amount``, ``risk_score``, ``confidence``, and optionally
                ``strategy``.
            balance: Current ledger balance (used for relative thresholds).

        Returns:
            A :class:`RiskReview` with the risk level and approval status.
        """
        action = decision.get("action", "unknown")
        amount = float(decision.get("amount", 0.0))
        risk_score = float(decision.get("risk_score", 0.0))
        confidence = float(decision.get("confidence", 0.0))
        strategy = decision.get("strategy", "")

        # Absolute risk ceiling — never allow decisions above this
        if risk_score >= self._absolute_risk_limit:
            review = RiskReview(
                decision_id=self._hash_decision(decision),
                risk_level=RiskLevel.CRITICAL,
                approved=False,
                required_approvals=self._critical_approvals,
                current_approvals=0,
                reason=f"Absolute risk limit exceeded ({risk_score:.2f} >= {self._absolute_risk_limit:.2f})",
                metadata={"action": action, "amount": amount},
            )
            self._persist_review(review, decision)
            self._log_review(review)
            return review

        # Determine risk level from score and amount
        risk_level = self._classify_risk(risk_score, amount, balance)
        required = self._required_approvals(risk_level)
        decision_id = self._hash_decision(decision)

        # Check if this exact decision already has pending approvals
        existing = self._get_pending(decision_id)
        if existing is not None:
            current = len(existing.approvals)
            approved = current >= required
            review = RiskReview(
                decision_id=decision_id,
                risk_level=risk_level,
                approved=approved,
                required_approvals=required,
                current_approvals=current,
                reason="Decision already under review" if not approved else "Approved",
                metadata={"action": action, "amount": amount},
            )
            self._log_review(review)
            return review

        # SpendGuard integration
        if self._spend_guard is not None and amount > 0:
            allowed, reason = self._spend_guard.quote_spend(strategy or "general", amount)
            if not allowed:
                review = RiskReview(
                    decision_id=decision_id,
                    risk_level=risk_level,
                    approved=False,
                    required_approvals=required,
                    current_approvals=0,
                    reason=f"SpendGuard blocked: {reason}",
                    metadata={"action": action, "amount": amount},
                )
                self._persist_review(review, decision)
                self._log_review(review)
                return review

        # LOW risk = auto-approve
        if risk_level == RiskLevel.LOW:
            review = RiskReview(
                decision_id=decision_id,
                risk_level=risk_level,
                approved=True,
                required_approvals=0,
                current_approvals=0,
                reason="Auto-approved (low risk)",
                metadata={"action": action, "amount": amount},
            )
            self._persist_review(review, decision)
            self._log_review(review)
            return review

        # MEDIUM / HIGH / CRITICAL = requires approvals
        review = RiskReview(
            decision_id=decision_id,
            risk_level=risk_level,
            approved=False,
            required_approvals=required,
            current_approvals=0,
            reason=f"Requires {required} approval(s) for {risk_level.name.lower()} risk",
            metadata={"action": action, "amount": amount},
        )
        self._persist_review(review, decision, status="pending")
        self._log_review(review)

        # CRITICAL risk also triggers an alert but does NOT auto-pause
        # (the caller can decide to use emergency_kill_switch)
        if risk_level == RiskLevel.CRITICAL:
            self._alert_critical(review)

        return review

    def approve(self, decision_id: str, approver_id: str) -> RiskReview:
        """Record an approval for a pending decision.

        Args:
            decision_id: The unique decision identifier.
            approver_id: Identity of the approving party.

        Returns:
            Updated :class:`RiskReview` reflecting the new approval count.
        """
        pending = self._get_pending(decision_id)
        if pending is None:
            review = RiskReview(
                decision_id=decision_id,
                risk_level=RiskLevel.LOW,
                approved=False,
                required_approvals=0,
                current_approvals=0,
                reason="Decision not found or already resolved",
            )
            self._log_review(review)
            return review

        # Idempotent: ignore duplicate approvals from the same approver
        if any(a[0] == approver_id for a in pending.approvals):
            current = len(pending.approvals)
            required = pending.required_approvals
            approved = current >= required
            review = RiskReview(
                decision_id=decision_id,
                risk_level=RiskLevel[pending.metadata.get("risk_level", "LOW")]
                if "risk_level" in pending.metadata
                else RiskLevel.LOW,
                approved=approved,
                required_approvals=required,
                current_approvals=current,
                reason="Approved" if approved else f"{current}/{required} approvals",
                metadata={"approver": approver_id, "note": "duplicate_ignored"},
            )
            self._log_review(review)
            return review

        # Record approval
        now = self._now().isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO risk_approvals (decision_id, approver_id, approved_at) VALUES (?, ?, ?);",
                (decision_id, approver_id, now),
            )
            conn.execute(
                """
                UPDATE risk_reviews
                SET approval_count = (SELECT COUNT(*) FROM risk_approvals WHERE decision_id = ?),
                    status = CASE WHEN approval_count >= required_approvals THEN 'approved' ELSE 'pending' END
                WHERE decision_id = ?;
                """,
                (decision_id, decision_id),
            )

        # Re-fetch
        refreshed = self._get_pending(decision_id)
        current = len(refreshed.approvals) if refreshed else 0
        required = refreshed.required_approvals if refreshed else 0
        approved = current >= required

        review = RiskReview(
            decision_id=decision_id,
            risk_level=RiskLevel[pending.metadata.get("risk_level", "LOW")]
            if "risk_level" in pending.metadata
            else RiskLevel.LOW,
            approved=approved,
            required_approvals=required,
            current_approvals=current,
            reason="Approved" if approved else f"{current}/{required} approvals",
            metadata={"approver": approver_id},
        )
        self._log_review(review)
        return review

    def get_pending_decisions(self) -> list[PendingDecision]:
        """Return all decisions currently awaiting approval."""
        decisions: list[PendingDecision] = []
        cutoff = (self._now() - timedelta(hours=self._pending_ttl_hours)).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT decision_id, action, amount, risk_score, confidence, strategy,
                       risk_level, required_approvals, requested_at, metadata_json
                FROM risk_reviews
                WHERE status = 'pending' AND requested_at > ?;
                """,
                (cutoff,),
            ).fetchall()
        for row in rows:
            approvals = self._get_approvals(row[0])
            decisions.append(PendingDecision(
                decision_id=row[0],
                action=row[1],
                amount=row[2],
                risk_score=row[3],
                confidence=row[4],
                strategy=row[5],
                requested_at=datetime.fromisoformat(row[8]),
                required_approvals=row[7],
                approvals=approvals,
                metadata=json.loads(row[9]),
            ))
        return decisions

    def get_review_history(
        self,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return historical risk reviews."""
        query = """
            SELECT decision_id, action, amount, risk_level, required_approvals,
                   approval_count, status, requested_at, resolved_at
            FROM risk_reviews
        """
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id DESC LIMIT ?"
        params += (limit,)

        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "decision_id": r[0],
                "action": r[1],
                "amount": r[2],
                "risk_level": r[3],
                "required_approvals": r[4],
                "approval_count": r[5],
                "status": r[6],
                "requested_at": r[7],
                "resolved_at": r[8],
            }
            for r in rows
        ]

    def emergency_kill_switch(self, reason: str) -> dict[str, Any]:
        """Trigger the emergency kill switch.

        Actions taken:
        1. Pause all spending via SpendGuard.
        2. Pause coordinator-level operations.
        3. Revoke all vault keys.
        4. Log a critical audit entry.
        """
        results: dict[str, Any] = {}

        if self._spend_guard is not None:
            self._spend_guard.pause(f"EMERGENCY KILL SWITCH: {reason}")
            results["spend_guard"] = "paused"

        if self._execution_env is not None:
            self._execution_env.pause(f"EMERGENCY KILL SWITCH: {reason}")
            results["execution_env"] = "paused"
            try:
                asyncio.get_running_loop()
                asyncio.create_task(self._execution_env.revoke_all_keys(reason))
                results["vault_revocation"] = "scheduled"
            except RuntimeError:
                # No running loop — use sync path if possible
                results["vault_revocation"] = "no_event_loop"
        else:
            results["execution_env"] = "not_configured"

        if self._audit_log is not None:
            self._audit_log.log(
                "emergency_kill_switch",
                {"reason": reason, "results": results},
                severity="critical",
            )

        return {"triggered": True, "reason": reason, **results}

    def is_kill_switch_active(self) -> bool:
        """Return True if any emergency pause is active."""
        if self._spend_guard is not None:
            paused, _ = self._spend_guard.is_paused()
            if paused:
                return True
        if self._execution_env is not None:
            paused, _ = self._execution_env.is_paused()
            if paused:
                return True
        return False

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _classify_risk(self, risk_score: float, amount: float, balance: float) -> RiskLevel:
        """Classify a decision into a risk level based on score and amount."""
        # Relative thresholds: if balance is tiny, even small amounts are high risk
        relative_amount = amount / max(balance, 1.0)

        if risk_score >= self._critical_threshold or amount >= self._critical_amount or relative_amount > 0.50:
            return RiskLevel.CRITICAL
        if risk_score >= self._high_threshold or amount >= self._high_amount or relative_amount > 0.20:
            return RiskLevel.HIGH
        if risk_score >= self._medium_threshold or amount >= self._medium_amount or relative_amount > 0.05:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _required_approvals(self, level: RiskLevel) -> int:
        mapping = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: self._medium_approvals,
            RiskLevel.HIGH: self._high_approvals,
            RiskLevel.CRITICAL: self._critical_approvals,
        }
        return mapping.get(level, 0)

    def _persist_review(
        self,
        review: RiskReview,
        decision: dict[str, Any],
        status: str = "resolved",
    ) -> None:
        now = self._now().isoformat()
        resolved = now if review.approved else None
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO risk_reviews (
                    decision_id, action, amount, risk_score, confidence, strategy,
                    risk_level, required_approvals, approval_count, status,
                    requested_at, resolved_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    risk_level = excluded.risk_level,
                    required_approvals = excluded.required_approvals,
                    approval_count = excluded.approval_count,
                    status = excluded.status,
                    resolved_at = excluded.resolved_at;
                """,
                (
                    review.decision_id,
                    decision.get("action", "unknown"),
                    float(decision.get("amount", 0.0)),
                    float(decision.get("risk_score", 0.0)),
                    float(decision.get("confidence", 0.0)),
                    decision.get("strategy", ""),
                    review.risk_level.name,
                    review.required_approvals,
                    review.current_approvals,
                    "approved" if review.approved else status,
                    now,
                    resolved,
                    json.dumps(decision, sort_keys=True, separators=(",", ":")),
                ),
            )

    def _get_pending(self, decision_id: str) -> PendingDecision | None:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT decision_id, action, amount, risk_score, confidence, strategy,
                       risk_level, required_approvals, requested_at, metadata_json
                FROM risk_reviews
                WHERE decision_id = ? AND status = 'pending';
                """,
                (decision_id,),
            ).fetchone()
        if row is None:
            return None
        approvals = self._get_approvals(decision_id)
        return PendingDecision(
            decision_id=row[0],
            action=row[1],
            amount=row[2],
            risk_score=row[3],
            confidence=row[4],
            strategy=row[5],
            requested_at=datetime.fromisoformat(row[8]),
            required_approvals=row[7],
            approvals=approvals,
            metadata=json.loads(row[9]),
        )

    def _get_approvals(self, decision_id: str) -> list[tuple[str, datetime]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT approver_id, approved_at FROM risk_approvals WHERE decision_id = ?;",
                (decision_id,),
            ).fetchall()
        return [(r[0], datetime.fromisoformat(r[1])) for r in rows]

    def _log_review(self, review: RiskReview) -> None:
        if self._audit_log is None:
            return
        self._audit_log.log(
            "risk_review",
            {
                "decision_id": review.decision_id,
                "risk_level": review.risk_level.name,
                "approved": review.approved,
                "required_approvals": review.required_approvals,
                "current_approvals": review.current_approvals,
                "reason": review.reason,
            },
            severity="info" if review.approved else "warning",
        )

    def _alert_critical(self, review: RiskReview) -> None:
        if self._audit_log is None:
            return
        self._audit_log.log(
            "risk_critical_alert",
            {
                "decision_id": review.decision_id,
                "risk_level": review.risk_level.name,
                "required_approvals": review.required_approvals,
                "reason": review.reason,
            },
            severity="critical",
        )
