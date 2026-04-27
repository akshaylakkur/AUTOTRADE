"""Tier-gated configuration for ÆON capabilities."""

from __future__ import annotations

from enum import Enum, auto

from auton.core.constants import TIER_THRESHOLDS


class Capability(Enum):
    """Operational capabilities gated by balance tier."""

    SPOT_TRADING = auto()
    FUTURES_TRADING = auto()
    EQUITIES = auto()
    FOREX = auto()
    OPTIONS = auto()
    SENTIMENT_FEEDS = auto()
    ON_CHAIN_DATA = auto()
    DEEP_REASONING = auto()
    SAAS_HOSTING = auto()
    FREELANCE_TASKS = auto()
    NEWSLETTER_SUBSCRIPTIONS = auto()
    SOFTWARE_LICENSING = auto()
    MULTI_EXCHANGE_ARBITRAGE = auto()
    STRATEGY_BACKTESTING = auto()
    SPOT_COMPUTE_SCALING = auto()
    HIRE_CONTRACTORS = auto()
    CROSS_BORDER_ARBITRAGE = auto()
    CUSTOM_ALGORITHM_DEPLOYMENT = auto()
    HIGH_FREQUENCY_DATA = auto()
    EXTERNAL_AI_AGENTS = auto()
    VENTURE_REINVESTMENT = auto()
    LEGAL_ENTITY_FORMATION = auto()


_CAPABILITY_TIER_MAP: dict[Capability, int] = {
    Capability.SPOT_TRADING: 0,
    Capability.FREELANCE_TASKS: 0,
    Capability.NEWSLETTER_SUBSCRIPTIONS: 0,
    Capability.FUTURES_TRADING: 1,
    Capability.MULTI_EXCHANGE_ARBITRAGE: 1,
    Capability.ON_CHAIN_DATA: 1,
    Capability.SAAS_HOSTING: 1,
    Capability.EQUITIES: 2,
    Capability.SENTIMENT_FEEDS: 2,
    Capability.DEEP_REASONING: 2,
    Capability.SOFTWARE_LICENSING: 2,
    Capability.FOREX: 3,
    Capability.OPTIONS: 3,
    Capability.STRATEGY_BACKTESTING: 3,
    Capability.SPOT_COMPUTE_SCALING: 3,
    Capability.HIRE_CONTRACTORS: 3,
    Capability.CROSS_BORDER_ARBITRAGE: 4,
    Capability.CUSTOM_ALGORITHM_DEPLOYMENT: 4,
    Capability.HIGH_FREQUENCY_DATA: 4,
    Capability.EXTERNAL_AI_AGENTS: 4,
    Capability.VENTURE_REINVESTMENT: 4,
    Capability.LEGAL_ENTITY_FORMATION: 4,
}


class TierGate:
    """Determines allowed capabilities based on current balance."""

    @staticmethod
    def get_tier(balance: float) -> int:
        """Return the highest tier unlocked by the given balance.

        Args:
            balance: Current realized balance.

        Returns:
            The highest tier number (0-4) the balance qualifies for.
        """
        tier = -1
        for t, threshold in sorted(TIER_THRESHOLDS.items()):
            if balance >= threshold:
                tier = t
            else:
                break
        return max(tier, 0)

    @classmethod
    def is_allowed(cls, capability: Capability, balance: float) -> bool:
        """Check whether a capability is allowed at the given balance.

        Args:
            capability: The capability to check.
            balance: Current realized balance.

        Returns:
            True if the capability is allowed.
        """
        required_tier = _CAPABILITY_TIER_MAP.get(capability, 0)
        return cls.get_tier(balance) >= required_tier

    @classmethod
    def allowed_capabilities(cls, balance: float) -> set[Capability]:
        """Return the set of all capabilities allowed at the given balance.

        Args:
            balance: Current realized balance.

        Returns:
            A set of allowed Capability enums.
        """
        current_tier = cls.get_tier(balance)
        return {
            cap
            for cap, required in _CAPABILITY_TIER_MAP.items()
            if current_tier >= required
        }
