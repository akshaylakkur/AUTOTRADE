"""Tier-gated configuration for ÆON capabilities."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


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

    # Ollama (local LLM)
    AEON_LLM_PROVIDER: str = os.environ.get("AEON_LLM_PROVIDER", "ollama")
    OLLAMA_HOST: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "llama3.2")

    # Amazon Bedrock
    BEDROCK_AWS_ACCESS_KEY_ID: str = os.environ.get("BEDROCK_AWS_ACCESS_KEY_ID", "")
    BEDROCK_AWS_SECRET_ACCESS_KEY: str = os.environ.get("BEDROCK_AWS_SECRET_ACCESS_KEY", "")
    BEDROCK_AWS_REGION: str = os.environ.get("BEDROCK_AWS_REGION", "us-east-1")
    BEDROCK_MODEL_ID: str = os.environ.get(
        "BEDROCK_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0"
    )

    GUIDANCE_PROMPT: str = os.environ.get(
        "AEON_GUIDANCE_PROMPT",
        "General profit: find any opportunity to grow the seed balance.",
    )

    EMAIL_CONFIG: dict[str, Any] = {
        "smtp_host": os.environ.get("AEON_APPROVAL_EMAIL_SMTP_HOST", ""),
        "smtp_port": int(os.environ.get("AEON_APPROVAL_EMAIL_SMTP_PORT", "587")),
        "sender_email": os.environ.get("AEON_APPROVAL_EMAIL_SENDER", ""),
        "sender_password": os.environ.get("AEON_APPROVAL_EMAIL_PASSWORD", ""),
        "recipient_email": os.environ.get("AEON_APPROVAL_EMAIL_RECIPIENT", ""),
        "use_tls": _env_bool("AEON_APPROVAL_EMAIL_USE_TLS", True),
    }

    # External API keys
    BINANCE_API_KEY: str = os.environ.get("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.environ.get("BINANCE_SECRET_KEY", "")
    STRIPE_SECRET_KEY: str = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET: str = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    SERPAPI_KEY: str = os.environ.get("SERPAPI_KEY", "")
    BRAVE_API_KEY: str = os.environ.get("BRAVE_API_KEY", "")
    PLAID_CLIENT_ID: str = os.environ.get("PLAID_CLIENT_ID", "")
    PLAID_SECRET: str = os.environ.get("PLAID_SECRET", "")
    TWITTER_BEARER_TOKEN: str = os.environ.get("TWITTER_BEARER_TOKEN", "")

    @classmethod
    def has_search(cls) -> bool:
        """True if a web search provider is available.

        DuckDuckGo is always available without an API key, so this
        returns ``True`` by default unless explicitly disabled.
        """
        return _env_bool("AEON_HAS_SEARCH", True)

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


class CapabilityRegistry:
    """Tracks which external capabilities are available based on env vars.

    Each capability maps to one or more environment variables that must be
    present (non-empty) for the capability to be considered available.
    """

    _CAPABILITY_MAP: dict[str, tuple[str, ...]] = {
        "binance_trading": ("BINANCE_API_KEY", "BINANCE_SECRET_KEY"),
        "binance_market_data": (),
        "stripe_payments": ("STRIPE_SECRET_KEY",),
        "email": ("AEON_APPROVAL_EMAIL_SMTP_HOST", "AEON_APPROVAL_EMAIL_SENDER", "AEON_APPROVAL_EMAIL_PASSWORD"),
        "serpapi_search": ("SERPAPI_KEY",),
        "brave_search": ("BRAVE_API_KEY",),
        "bedrock_llm": ("BEDROCK_AWS_ACCESS_KEY_ID", "BEDROCK_AWS_SECRET_ACCESS_KEY"),
        "ollama_llm": (),
        "plaid_banking": ("PLAID_CLIENT_ID", "PLAID_SECRET"),
        "twitter_sentiment": ("TWITTER_BEARER_TOKEN",),
    }

    @classmethod
    def is_available(cls, capability: str) -> bool:
        """Return True if *capability* is configured (all required env vars present).

        Args:
            capability: The capability name (e.g. ``"binance_trading"``).

        Returns:
            True when every required environment variable is non-empty.
        """
        required = cls._CAPABILITY_MAP.get(capability, ())
        if not required:
            return True
        return all(os.environ.get(var, "").strip() for var in required)

    @classmethod
    def check_all(cls) -> dict[str, bool]:
        """Return availability for every registered capability.

        Returns:
            A mapping of capability name → availability boolean.
        """
        return {cap: cls.is_available(cap) for cap in cls._CAPABILITY_MAP}

    @classmethod
    def register_capability(cls, name: str, *env_vars: str) -> None:
        """Register a new capability and its required environment variables.

        Args:
            name: Capability identifier.
            *env_vars: Environment variable names that must be present.
        """
        cls._CAPABILITY_MAP[name] = env_vars

    @classmethod
    def missing_vars(cls, capability: str) -> list[str]:
        """Return the list of missing environment variables for a capability.

        Args:
            capability: The capability name to inspect.

        Returns:
            A list of environment variable names that are empty or unset.
        """
        required = cls._CAPABILITY_MAP.get(capability, ())
        return [var for var in required if not os.environ.get(var, "").strip()]

    @classmethod
    def log_status(cls) -> None:
        """Log the availability status of all registered capabilities."""
        for cap, available in cls.check_all().items():
            level = logging.INFO if available else logging.WARNING
            logger.log(level, "Capability '%s': %s", cap, "available" if available else "unavailable")
