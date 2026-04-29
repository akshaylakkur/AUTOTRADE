"""Typed event dataclasses for the ÆON event bus."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class BalanceChanged:
    """Emitted when the master balance changes."""

    old_balance: float
    new_balance: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TierChanged:
    """Emitted when the operational tier changes."""

    old_tier: int
    new_tier: int
    balance: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class TradeSignal:
    """Emitted when the Cortex generates a trade signal."""

    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: float
    price: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CostIncurred:
    """Emitted when an operational cost is deducted."""

    amount: float
    category: str  # e.g. "inference", "data", "compute", "trading_fees"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    description: str = ""


@dataclass(frozen=True, slots=True)
class EmergencyLiquidate:
    """Emitted when the Reflexes trigger emergency liquidation."""

    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    positions: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Hibernate:
    """Emitted when the system enters hibernation."""

    reason: str
    duration_seconds: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class Shutdown:
    """Emitted when the system initiates a graceful shutdown."""

    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    final_balance: float | None = None


@dataclass(frozen=True, slots=True)
class DataReceived:
    """Emitted when the Senses ingest new data."""

    source: str
    data_type: str
    payload: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ReflexTriggered:
    """Emitted when a reflex (execution layer) action fires."""

    reflex_name: str
    payload: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class CodeModified:
    """Emitted when a self-modification is successfully applied."""

    patch_id: str
    target_file: str
    author: str
    reason: str
    cost: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ModificationFailed:
    """Emitted when a self-modification fails and is rolled back."""

    patch_id: str
    target_file: str
    reason: str
    rolled_back: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class DependencyInstalled:
    """Emitted when a Python dependency is successfully installed."""

    package: str
    version: str
    cost: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class MessageReceived:
    """Emitted when the Senses ingest a message (email or SMS)."""

    source: str  # "email" or "sms"
    sender: str
    subject: str
    body: str
    raw_payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class VerificationCodeReceived:
    """Emitted when an external verification code (e.g., 2FA, email) is received."""

    source: str
    code: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class NotificationSent:
    """Emitted when a notification is dispatched."""

    channel: str  # "email" or "sms"
    alert_type: str
    recipient: str
    status: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class SubscriptionPurchased:
    """Emitted when a SaaS or API subscription is purchased."""

    service: str
    tier: str
    cost: float
    billing_cycle: str  # e.g., "monthly", "yearly"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ProductDeployed:
    """Emitted when a product or service is deployed."""

    product_name: str
    version: str
    environment: str  # e.g., "production", "staging"
    cost: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class BankTransactionDetected:
    """Emitted when a new bank or payment transaction is detected."""

    amount: float
    currency: str
    direction: str  # "incoming" or "outgoing"
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class OpportunityDiscovered:
    """Emitted when the system discovers a new revenue or arbitrage opportunity."""

    domain: str  # e.g., "trading", "freelance", "saas", "arbitrage"
    description: str
    estimated_value: float
    confidence: float  # 0.0 to 1.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class DecisionMade:
    """Emitted when the decision engine commits to a concrete action."""

    action: str
    expected_roi: float
    confidence: float
    risk_score: float
    required_budget: float
    strategy: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StrategySwitched:
    """Emitted when the expansion controller changes active strategies."""

    old_strategies: list[str]
    new_strategies: list[str]
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class SimulationCompleted:
    """Emitted when a consequence simulation or backtest finishes."""

    simulation_type: str
    mean_outcome: float
    worst_case: float
    best_case: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GoalGenerated:
    """Emitted when the free-will module generates a new autonomous goal."""

    goal_name: str
    description: str
    target_value: float
    unit: str
    deadline: datetime | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ActionProposed:
    """Emitted when a financial or deploy action is proposed for human approval."""

    proposal_id: str
    action_type: str  # "trade", "deploy", "spend"
    payload: dict[str, Any]
    risk_level: str
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ActionApproved:
    """Emitted when a human approves a proposed action."""

    proposal_id: str
    approver: str
    approved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ActionRejected:
    """Emitted when a human rejects a proposed action."""

    proposal_id: str
    approver: str
    reason: str = ""
    rejected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ActionExecuted:
    """Emitted when an approved action is actually executed."""

    proposal_id: str
    action_type: str
    payload: dict[str, Any]
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class ActionExpired:
    """Emitted when a proposed action expires without approval."""

    proposal_id: str
    action_type: str
    expired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class EnvironmentalUpdate:
    """Emitted periodically by the environmental sensor."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    timezone: str = "UTC"
    utc_offset: float = 0.0
    market_hours: dict[str, bool] = field(default_factory=dict)
    economic_calendar: list[dict[str, Any]] = field(default_factory=list)
    system_load: dict[str, float] = field(default_factory=dict)
    network_health: dict[str, Any] = field(default_factory=dict)
