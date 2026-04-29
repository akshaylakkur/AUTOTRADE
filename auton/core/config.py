"""Tier-gated configuration for ÆON capabilities."""

from __future__ import annotations

import os
from enum import Enum, auto
from typing import Any

from auton.core.constants import RESTRICTED_MODE, TIER_THRESHOLDS

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore[assignment]
else:
    load_dotenv()


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
    INTELLIGENCE_RESEARCH = auto()


_CAPABILITY_TIER_MAP: dict[Capability, int] = {
    Capability.SPOT_TRADING: 0,
    Capability.FREELANCE_TASKS: 0,
    Capability.NEWSLETTER_SUBSCRIPTIONS: 0,
    Capability.INTELLIGENCE_RESEARCH: 0,
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


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    value = os.environ.get(name, "")
    if value == "":
        return default
    return value.lower() in ("1", "true", "yes", "on")


class AeonConfig:
    """Global runtime configuration for ÆON.

    Loads from environment variables (highest priority) then falls back to a
    ``.env`` file in the project root.  When ``RESTRICTED_MODE`` is enabled,
    all financial and deployment actions require human email approval.
    """

    RESTRICTED_MODE: bool = _env_bool("AEON_RESTRICTED_MODE", RESTRICTED_MODE)

    EMAIL_CONFIG: dict[str, Any] = {
        "smtp_host": os.environ.get("AEON_APPROVAL_EMAIL_SMTP_HOST", ""),
        "smtp_port": int(os.environ.get("AEON_APPROVAL_EMAIL_SMTP_PORT", "587")),
        "sender_email": os.environ.get("AEON_APPROVAL_EMAIL_SENDER", ""),
        "sender_password": os.environ.get("AEON_APPROVAL_EMAIL_PASSWORD", ""),
        "recipient_email": os.environ.get("AEON_APPROVAL_EMAIL_RECIPIENT", ""),
        "use_tls": _env_bool("AEON_APPROVAL_EMAIL_USE_TLS", True),
    }

    @classmethod
    def validate(cls) -> None:
        """Raise RuntimeError if restricted mode is enabled but email is missing."""
        if not cls.RESTRICTED_MODE:
            return
        required = ("smtp_host", "sender_email", "sender_password", "recipient_email")
        missing = [k for k in required if not cls.EMAIL_CONFIG.get(k)]
        if missing:
            raise RuntimeError(
                f"RESTRICTED_MODE is enabled but email config is incomplete. "
                f"Missing keys: {', '.join(missing)}"
            )
