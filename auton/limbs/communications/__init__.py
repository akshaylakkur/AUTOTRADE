"""Outbound communications for Project ÆON — unified API."""

from __future__ import annotations

from typing import Any

from auton.limbs.base_limb import BaseLimb

from .email_client import ActionProposal, EmailClient, SMTPConfig
from .imap_listener import IMAPConfig, IMAPListener
from .notifications import NotificationDispatcher
from .queue import EmailQueue
from .verification_extractor import VerificationCodeExtractor

__all__ = [
    "ActionProposal",
    "CommunicationsHub",
    "EmailClient",
    "EmailQueue",
    "IMAPConfig",
    "IMAPListener",
    "NotificationDispatcher",
    "SMTPConfig",
    "VerificationCodeExtractor",
]


class CommunicationsHub(BaseLimb):
    """Unified wrapper that exposes notification dispatch and
    verification extraction as a single limb.
    """

    def __init__(
        self,
        *,
        email_provider: str = "smtp",
        event_bus: Any | None = None,
        ledger: Any | None = None,
        tier_gate: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(event_bus=event_bus, ledger=ledger, tier_gate=tier_gate)
        self._dispatcher = NotificationDispatcher(
            email_provider=email_provider,
            event_bus=event_bus,
            ledger=ledger,
            tier_gate=tier_gate,
            **kwargs,
        )
        self._extractor = VerificationCodeExtractor(
            event_bus=event_bus,
            ledger=ledger,
            tier_gate=tier_gate,
        )

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        """Dispatch to the appropriate sub-component.

        Expected action shapes::

            {"subsystem": "notifications", "method": "dispatch", "kwargs": {...}}
            {"subsystem": "extractor", "method": "process_email", "kwargs": {...}}
        """
        subsystem = action.get("subsystem")
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if subsystem == "notifications":
            return await self._dispatcher.execute({"method": method, "kwargs": kwargs})
        if subsystem == "extractor":
            return await self._extractor.execute({"method": method, "kwargs": kwargs})
        raise ValueError(f"Unknown subsystem: {subsystem}")

    async def get_cost_estimate(self, action: Any) -> float:
        subsystem = action.get("subsystem")
        if subsystem == "notifications":
            return await self._dispatcher.get_cost_estimate(action)
        if subsystem == "extractor":
            return await self._extractor.get_cost_estimate(action)
        return 0.0

    def is_available(self, tier: int) -> bool:
        return self._dispatcher.is_available(tier) and self._extractor.is_available(tier)

    async def health_check(self) -> dict[str, Any]:
        d_health = await self._dispatcher.health_check()
        e_health = await self._extractor.health_check()
        return {
            "status": "healthy"
            if d_health.get("status", "").startswith("healthy") and e_health.get("status") == "healthy"
            else "degraded",
            "dispatcher": d_health,
            "extractor": e_health,
        }

    # ------------------------------------------------------------------ #
    # Unified convenience API
    # ------------------------------------------------------------------ #

    async def send_alert(
        self,
        alert_type: str,
        message: str,
        priority: str = "normal",
        recipients: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Convenience wrapper around ``NotificationDispatcher.dispatch``."""
        return await self._dispatcher.dispatch(alert_type, message, priority, recipients)

    async def extract_from_email(self, email_data: dict[str, Any]) -> str | None:
        """Convenience wrapper around ``VerificationCodeExtractor.process_email``."""
        return await self._extractor.process_email(email_data)

    async def extract_from_sms(self, sms_data: dict[str, Any]) -> str | None:
        """Convenience wrapper around ``VerificationCodeExtractor.process_sms``."""
        return await self._extractor.process_sms(sms_data)

    async def close(self) -> None:
        await self._dispatcher.close()

    async def __aenter__(self) -> CommunicationsHub:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
