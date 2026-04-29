"""Human Gateway — interception layer for financial and deployment actions.

When ``RESTRICTED_MODE=true`` (env var or explicit config), all gated actions
require human approval via email before they are forwarded to the real
executor.  When ``RESTRICTED_MODE=false`` the gateway is a transparent
pass-through middleware.

The gateway is **non-breaking**: existing executor APIs remain untouched and
can be used directly when the gateway is not in the call path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class ApprovalStatus(str, Enum):
    """Lifecycle of a human approval request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """Rich proposal sent to a human for approval."""

    proposal_id: str
    action_type: str
    action_payload: dict[str, Any]
    market_snapshot: dict[str, Any]
    pl_impact_estimate: float
    burn_rate_impact: float
    risk_score: float
    reasoning_summary: str
    environmental_context: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: ApprovalStatus = ApprovalStatus.PENDING


@dataclass(frozen=True, slots=True)
class ActionProposed:
    """Typed event emitted when a new proposal is created."""

    proposal: ActionProposal
    recipient: str
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class ActionExecuted:
    """Typed event emitted after an approved action is executed."""

    proposal_id: str
    action_type: str
    result_summary: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ActionRejected:
    """Typed event emitted when a proposal is rejected or expires."""

    proposal_id: str
    action_type: str
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HumanGatewayError(Exception):
    """Raised when the human gateway blocks or fails an action."""


class HumanGateway:
    """Middleware that gates financial and deployment actions behind human approval.

    Parameters
    ----------
    executor:
        The real limb / executor being wrapped (e.g. *BinanceSpotTradingLimb*,
        *DeploymentManager*, *StripePaymentsLimb*).
    event_bus:
        Optional event bus for emitting proposal lifecycle events.
    wallet:
        Optional *MasterWallet* for balance-aware impact estimates.
    risk_manager:
        Optional *RiskManager* for risk-score calculation.
    burn_analyzer:
        Optional *BurnAnalyzer* for burn-rate impact.
    pnl_engine:
        Optional *PnLEngine* for P&L impact estimation.
    restricted_mode:
        If ``True`` all gated actions require approval.  If ``None`` the
        value is read from ``RESTRICTED_MODE`` env var (default ``"true"``).
    approval_timeout_seconds:
        How long to wait for a human response before expiring the proposal.
    default_recipient:
        Email address (or other identifier) of the human approver.
    reasoning_callback:
        Optional callable ``(action_type, payload) -> str`` that returns a
        reasoning summary from the cortex / decision engine.
    market_data_callback:
        Optional callable ``() -> dict`` that returns a market snapshot.
    """

    _GATED_ACTIONS: set[str] = {
        "execute_trade",
        "deploy_product",
        "spend_funds",
        "provision_resource",
    }

    def __init__(
        self,
        executor: Any,
        *,
        event_bus: Any | None = None,
        wallet: Any | None = None,
        risk_manager: Any | None = None,
        burn_analyzer: Any | None = None,
        pnl_engine: Any | None = None,
        restricted_mode: bool | None = None,
        approval_timeout_seconds: float = 3600.0,
        default_recipient: str | None = None,
        reasoning_callback: Callable[[str, dict[str, Any]], str] | None = None,
        market_data_callback: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._executor = executor
        self._event_bus = event_bus
        self._wallet = wallet
        self._risk_manager = risk_manager
        self._burn_analyzer = burn_analyzer
        self._pnl_engine = pnl_engine
        self._restricted_mode = (
            restricted_mode
            if restricted_mode is not None
            else os.environ.get("RESTRICTED_MODE", "true").lower() in ("true", "1", "yes", "on")
        )
        self._approval_timeout = approval_timeout_seconds
        self._default_recipient = default_recipient or os.environ.get(
            "AEON_DEFAULT_EMAIL_RECIPIENT", ""
        )
        self._reasoning_callback = reasoning_callback
        self._market_data_callback = market_data_callback

        # In-memory proposal tracking
        self._pending: dict[str, asyncio.Event] = {}
        self._approvals: dict[str, ApprovalStatus] = {}

    # ------------------------------------------------------------------ #
    # Pass-through properties so the gateway looks like the wrapped executor
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return getattr(self._executor, "name", self._executor.__class__.__name__)

    def __getattr__(self, item: str) -> Any:
        """Transparently proxy non-gated attribute access to the executor."""
        return getattr(self._executor, item)

    # ------------------------------------------------------------------ #
    # Gated action APIs
    # ------------------------------------------------------------------ #

    async def execute_trade(self, **kwargs: Any) -> Any:
        """Intercepted trade execution.

        Maps to ``executor.place_order`` or ``executor.execute_trade``.
        """
        if not self._restricted_mode:
            return await self._forward("execute_trade", kwargs)
        return await self._request_approval("execute_trade", kwargs)

    async def deploy_product(self, **kwargs: Any) -> Any:
        """Intercepted product deployment.

        Maps to ``executor.deploy`` or ``executor.deploy_product``.
        """
        if not self._restricted_mode:
            return await self._forward("deploy_product", kwargs)
        return await self._request_approval("deploy_product", kwargs)

    async def spend_funds(self, **kwargs: Any) -> Any:
        """Intercepted fund spending.

        Maps to ``executor.create_payment_intent``, ``executor.create_invoice``,
        or ``executor.spend_funds``.
        """
        if not self._restricted_mode:
            return await self._forward("spend_funds", kwargs)
        return await self._request_approval("spend_funds", kwargs)

    async def provision_resource(self, **kwargs: Any) -> Any:
        """Intercepted resource provisioning.

        Maps to ``executor.provision_resource`` or ``executor.execute``.
        """
        if not self._restricted_mode:
            return await self._forward("provision_resource", kwargs)
        return await self._request_approval("provision_resource", kwargs)

    # ------------------------------------------------------------------ #
    # Forwarding helpers (duck-typed dispatch)
    # ------------------------------------------------------------------ #

    async def _forward(self, action_type: str, payload: dict[str, Any]) -> Any:
        """Pass the action directly to the underlying executor."""
        if action_type == "execute_trade":
            if hasattr(self._executor, "place_order"):
                return await self._executor.place_order(**payload)
            if hasattr(self._executor, "execute_trade"):
                return await self._executor.execute_trade(**payload)
            return await self._executor.execute({"method": "place_order", "kwargs": payload})

        if action_type == "deploy_product":
            if hasattr(self._executor, "deploy"):
                return await self._executor.deploy(**payload)
            if hasattr(self._executor, "deploy_product"):
                return await self._executor.deploy_product(**payload)
            return await self._executor.execute({"method": "deploy", "kwargs": payload})

        if action_type == "spend_funds":
            if hasattr(self._executor, "create_payment_intent"):
                return await self._executor.create_payment_intent(**payload)
            if hasattr(self._executor, "create_invoice"):
                return await self._executor.create_invoice(**payload)
            if hasattr(self._executor, "spend_funds"):
                return await self._executor.spend_funds(**payload)
            return await self._executor.execute({"method": "spend_funds", "kwargs": payload})

        if action_type == "provision_resource":
            if hasattr(self._executor, "provision_resource"):
                return await self._executor.provision_resource(**payload)
            return await self._executor.execute({"method": "provision_resource", "kwargs": payload})

        raise HumanGatewayError(f"Unknown action type: {action_type}")

    # ------------------------------------------------------------------ #
    # Approval flow
    # ------------------------------------------------------------------ #

    async def _request_approval(self, action_type: str, action_payload: dict[str, Any]) -> Any:
        """Build a proposal, notify the human, and await a decision."""
        proposal = self._build_proposal(action_type, action_payload)
        event = asyncio.Event()
        self._pending[proposal.proposal_id] = event
        self._approvals[proposal.proposal_id] = ApprovalStatus.PENDING

        await self._notify_human(proposal)

        try:
            await asyncio.wait_for(event.wait(), timeout=self._approval_timeout)
        except asyncio.TimeoutError:
            self._approvals[proposal.proposal_id] = ApprovalStatus.EXPIRED
            await self._emit_rejected(proposal, reason="timeout")
            raise HumanGatewayError(
                f"Approval timed out after {self._approval_timeout}s for {action_type}"
            ) from None
        finally:
            self._pending.pop(proposal.proposal_id, None)

        status = self._approvals.pop(proposal.proposal_id, ApprovalStatus.REJECTED)
        if status == ApprovalStatus.REJECTED:
            await self._emit_rejected(proposal, reason="human_rejected")
            raise HumanGatewayError(f"Action {action_type} rejected by human")

        # Approved — execute and report
        result = await self._forward(action_type, action_payload)
        await self._report_result(proposal, result)
        return result

    def _build_proposal(self, action_type: str, action_payload: dict[str, Any]) -> ActionProposal:
        """Assemble a rich *ActionProposal* with financial and environmental context."""
        proposal_id = f"PROP-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

        market_snapshot = self._gather_market_snapshot()
        pl_impact = self._estimate_pl_impact(action_type, action_payload)
        burn_impact = self._estimate_burn_rate_impact(action_type, action_payload)
        risk_score = self._compute_risk_score(action_type, action_payload)
        reasoning = self._gather_reasoning(action_type, action_payload)
        env_context = self._gather_environmental_context()

        return ActionProposal(
            proposal_id=proposal_id,
            action_type=action_type,
            action_payload=dict(action_payload),
            market_snapshot=market_snapshot,
            pl_impact_estimate=pl_impact,
            burn_rate_impact=burn_impact,
            risk_score=risk_score,
            reasoning_summary=reasoning,
            environmental_context=env_context,
        )

    async def _notify_human(self, proposal: ActionProposal) -> None:
        """Emit events so a notifier (e.g. CommunicationsHub) can email the human."""
        subject = f"[AEON] Approval Required: {proposal.action_type} — {proposal.proposal_id}"
        body = self._format_proposal_email(proposal)

        event = ActionProposed(
            proposal=proposal,
            recipient=self._default_recipient,
            subject=subject,
            body=body,
        )

        await self._publish_typed(ActionProposed, event)
        await self._publish_string(
            "human_gateway.proposal.pending",
            {
                "proposal_id": proposal.proposal_id,
                "action_type": proposal.action_type,
                "recipient": self._default_recipient,
                "subject": subject,
                "body": body,
                "risk_score": proposal.risk_score,
                "pl_impact": proposal.pl_impact_estimate,
            },
        )

        logger.info(
            "Human approval requested for %s (proposal=%s)",
            proposal.action_type,
            proposal.proposal_id,
        )

    async def _report_result(self, proposal: ActionProposal, result: Any) -> None:
        """Emit execution result events."""
        summary = self._summarize_result(result)
        event = ActionExecuted(
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            result_summary=summary,
        )
        await self._publish_typed(ActionExecuted, event)
        await self._publish_string(
            "human_gateway.action.executed",
            {
                "proposal_id": proposal.proposal_id,
                "action_type": proposal.action_type,
                "result_summary": summary,
            },
        )
        logger.info(
            "Action %s executed for proposal %s",
            proposal.action_type,
            proposal.proposal_id,
        )

    async def _emit_rejected(self, proposal: ActionProposal, reason: str) -> None:
        event = ActionRejected(
            proposal_id=proposal.proposal_id,
            action_type=proposal.action_type,
            reason=reason,
        )
        await self._publish_typed(ActionRejected, event)
        await self._publish_string(
            "human_gateway.action.rejected",
            {
                "proposal_id": proposal.proposal_id,
                "action_type": proposal.action_type,
                "reason": reason,
            },
        )
        logger.info(
            "Action %s rejected for proposal %s (reason=%s)",
            proposal.action_type,
            proposal.proposal_id,
            reason,
        )

    # ------------------------------------------------------------------ #
    # Public approval API (called by email parser / CLI / web hook)
    # ------------------------------------------------------------------ #

    def approve(self, proposal_id: str) -> bool:
        """Approve a pending proposal by ID.

        Returns ``True`` if the proposal existed and was pending.
        """
        event = self._pending.get(proposal_id)
        if event is None:
            return False
        if self._approvals.get(proposal_id) != ApprovalStatus.PENDING:
            return False
        self._approvals[proposal_id] = ApprovalStatus.APPROVED
        event.set()
        return True

    def reject(self, proposal_id: str) -> bool:
        """Reject a pending proposal by ID.

        Returns ``True`` if the proposal existed and was pending.
        """
        event = self._pending.get(proposal_id)
        if event is None:
            return False
        if self._approvals.get(proposal_id) != ApprovalStatus.PENDING:
            return False
        self._approvals[proposal_id] = ApprovalStatus.REJECTED
        event.set()
        return True

    def get_proposal_status(self, proposal_id: str) -> ApprovalStatus | None:
        """Return the current status of a proposal, or ``None`` if unknown."""
        return self._approvals.get(proposal_id)

    # ------------------------------------------------------------------ #
    # Context gathering (pluggable / overrideable)
    # ------------------------------------------------------------------ #

    def _gather_market_snapshot(self) -> dict[str, Any]:
        if self._market_data_callback is not None:
            try:
                return self._market_data_callback()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Market data callback failed: %s", exc)
        return {}

    def _estimate_pl_impact(self, action_type: str, action_payload: dict[str, Any]) -> float:
        if action_type == "execute_trade" and self._pnl_engine is not None:
            # Heuristic: approximate P&L impact from notional size
            qty = action_payload.get("quantity", 0.0)
            price = action_payload.get("price") or action_payload.get("entry_price", 0.0)
            return qty * price * 0.02  # rough 2% slippage / fee estimate
        if action_type == "spend_funds":
            amount = action_payload.get("amount", 0)
            # Stripe amount is in cents
            if amount > 1000:
                return amount / 100.0
            return float(amount)
        return 0.0

    def _estimate_burn_rate_impact(self, action_type: str, action_payload: dict[str, Any]) -> float:
        if action_type == "deploy_product":
            # Hosting costs roughly $5-10/mo per product
            return 7.5
        if action_type == "provision_resource":
            return action_payload.get("estimated_monthly_cost", 5.0)
        if action_type == "spend_funds":
            amount = action_payload.get("amount", 0)
            if amount > 1000:
                return amount / 100.0
            return float(amount)
        return 0.0

    def _compute_risk_score(self, action_type: str, action_payload: dict[str, Any]) -> float:
        if self._risk_manager is not None and hasattr(self._risk_manager, "max_position_size"):
            try:
                balance = self._wallet.get_balance() if self._wallet else 50.0
                assessment = self._risk_manager.max_position_size(
                    balance=balance,
                    tier=0,
                    edge=0.5,
                )
                return 1.0 - float(getattr(assessment, "approved", True))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Risk manager call failed: %s", exc)

        # Fallback heuristic
        notional = 0.0
        if action_type == "execute_trade":
            qty = action_payload.get("quantity", 0.0)
            price = action_payload.get("price") or action_payload.get("entry_price", 0.0)
            notional = qty * price
        elif action_type == "spend_funds":
            amount = action_payload.get("amount", 0)
            notional = amount / 100.0 if amount > 1000 else float(amount)
        elif action_type == "deploy_product":
            notional = action_payload.get("hosting_cost", 10.0)

        balance = self._wallet.get_balance() if self._wallet else 50.0
        if balance <= 0:
            return 1.0
        pct = notional / balance
        # Higher % of balance = higher risk score, clamped 0-1
        return min(1.0, max(0.0, pct * 5.0))

    def _gather_reasoning(self, action_type: str, action_payload: dict[str, Any]) -> str:
        if self._reasoning_callback is not None:
            try:
                return self._reasoning_callback(action_type, action_payload)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Reasoning callback failed: %s", exc)
        return f"Autonomous {action_type} requested by ÆON decision engine."

    def _gather_environmental_context(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "utc_time": now.isoformat(),
            "market_hours": self._infer_market_hours(now),
            "day_of_week": now.strftime("%A"),
        }

    @staticmethod
    def _infer_market_hours(dt: datetime) -> str:
        hour = dt.hour
        if 13 <= hour < 21:
            return "US_MARKET_OPEN"
        if 7 <= hour < 16:
            return "EU_MARKET_OPEN"
        if 0 <= hour < 9:
            return "ASIA_MARKET_OPEN"
        return "AFTER_HOURS"

    # ------------------------------------------------------------------ #
    # Event-bus helpers (supports typed and legacy string buses)
    # ------------------------------------------------------------------ #

    async def _publish_typed(self, event_type: type, payload: Any) -> None:
        if self._event_bus is None:
            return
        try:
            if hasattr(self._event_bus, "publish"):
                await self._event_bus.publish(event_type, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Typed event publish failed: %s", exc)

    async def _publish_string(self, event_name: str, payload: dict[str, Any]) -> None:
        if self._event_bus is None:
            return
        try:
            if hasattr(self._event_bus, "emit"):
                await self._event_bus.emit(event_name, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("String event emit failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_proposal_email(proposal: ActionProposal) -> str:
        return f"""\
ÆON Action Proposal — Human Approval Required
=============================================

Proposal ID:     {proposal.proposal_id}
Action Type:     {proposal.action_type}
Requested At:    {proposal.timestamp.isoformat()}
Status:          {proposal.status.value}

--- Reasoning ---
{proposal.reasoning_summary}

--- Financial Impact ---
P&L Impact Estimate:  ${proposal.pl_impact_estimate:,.4f}
Burn Rate Impact:     ${proposal.burn_rate_impact:,.4f}
Risk Score:           {proposal.risk_score:.2f} / 1.0

--- Environmental Context ---
{proposal.environmental_context}

--- Action Payload ---
{proposal.action_payload}

Reply with one of:
  APPROVE {proposal.proposal_id}
  REJECT  {proposal.proposal_id}
"""

    @staticmethod
    def _summarize_result(result: Any) -> str:
        if isinstance(result, dict):
            return str(result)[:500]
        if hasattr(result, "__dict__"):
            return str(result.__dict__)[:500]
        return str(result)[:500]
