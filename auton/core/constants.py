"""Core constants for the ÆON autonomous economic agent."""

from __future__ import annotations

from typing import Final

# Tier thresholds: minimum balance required to unlock each tier
TIER_THRESHOLDS: Final[dict[int, float]] = {
    0: 50.0,
    1: 100.0,
    2: 500.0,
    3: 2500.0,
    4: 10000.0,
}

# Daily compute budget allocated per tier (in USD)
TIER_COMPUTE_BUDGETS: Final[dict[int, float]] = {
    0: 0.50,
    1: 1.00,
    2: 5.00,
    3: 20.00,
    4: 100.00,
}

# Risk limits per tier
# Each tier defines:
#   max_position_pct: max % of balance in a single position
#   max_leverage: max leverage multiplier
#   max_daily_trades: maximum number of new trades per day
#   survival_reserve_pct: minimum % of balance kept as untouchable reserve
RISK_LIMITS: Final[dict[int, dict[str, float]]] = {
    0: {
        "max_position_pct": 0.02,
        "max_leverage": 1.0,
        "max_daily_trades": 5.0,
        "survival_reserve_pct": 0.10,
    },
    1: {
        "max_position_pct": 0.02,
        "max_leverage": 2.0,
        "max_daily_trades": 10.0,
        "survival_reserve_pct": 0.10,
    },
    2: {
        "max_position_pct": 0.02,
        "max_leverage": 2.0,
        "max_daily_trades": 20.0,
        "survival_reserve_pct": 0.10,
    },
    3: {
        "max_position_pct": 0.01,
        "max_leverage": 5.0,
        "max_daily_trades": 50.0,
        "survival_reserve_pct": 0.10,
    },
    4: {
        "max_position_pct": 0.01,
        "max_leverage": 10.0,
        "max_daily_trades": 100.0,
        "survival_reserve_pct": 0.10,
    },
}

# Default survival reserve percentage
DEFAULT_SURVIVAL_RESERVE_PCT: Final[float] = 0.10

# Default maximum daily drawdown before hibernation trigger
DEFAULT_MAX_DAILY_DRAWDOWN: Final[float] = 0.10

# Seed balance at system initialization
SEED_BALANCE: Final[float] = 50.00
