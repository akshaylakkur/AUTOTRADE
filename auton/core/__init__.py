"""Core runtime for the ÆON autonomous economic agent."""

from __future__ import annotations

from auton.core.config import Capability, TierGate
from auton.core.constants import (
    DEFAULT_MAX_DAILY_DRAWDOWN,
    DEFAULT_SURVIVAL_RESERVE_PCT,
    RISK_LIMITS,
    SEED_BALANCE,
    TIER_COMPUTE_BUDGETS,
    TIER_THRESHOLDS,
)
from auton.core.event_bus import EventBus
from auton.core.events import (
    BalanceChanged,
    CostIncurred,
    DataReceived,
    EmergencyLiquidate,
    Hibernate,
    ReflexTriggered,
    Shutdown,
    TierChanged,
    TradeSignal,
)
from auton.core.state_machine import State, StateMachine

__all__ = [
    "Capability",
    "TierGate",
    "DEFAULT_MAX_DAILY_DRAWDOWN",
    "DEFAULT_SURVIVAL_RESERVE_PCT",
    "RISK_LIMITS",
    "SEED_BALANCE",
    "TIER_COMPUTE_BUDGETS",
    "TIER_THRESHOLDS",
    "EventBus",
    "BalanceChanged",
    "CostIncurred",
    "DataReceived",
    "EmergencyLiquidate",
    "Hibernate",
    "ReflexTriggered",
    "Shutdown",
    "TierChanged",
    "TradeSignal",
    "State",
    "StateMachine",
]
