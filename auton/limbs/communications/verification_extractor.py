"""Extract verification codes from emails and SMS and forward via event bus."""

from __future__ import annotations

import logging
import re
from typing import Any

from auton.limbs.base_limb import BaseLimb

logger = logging.getLogger(__name__)


class VerificationCodeExtractor(BaseLimb):
    """Pattern-matching extractor for OTPs and verification codes.

    Supports common formats:
    - 6-digit numeric codes
    - ``code is XXXX`` / ``your code: XXXX``
    - ``OTP is XXXX``
    - alphanumeric codes (4–8 chars)
    """

    DEFAULT_PATTERNS: list[str] = [
        r"(?i)code\s+is\s+([A-Za-z0-9]{4,8})",
        r"(?i)code:\s*([A-Za-z0-9]{4,8})",
        r"(?i)verification\s+code\s+is\s+([A-Za-z0-9]{4,8})",
        r"(?i)your\s+code\s+is\s+([A-Za-z0-9]{4,8})",
        r"(?i)otp\s+is\s+([A-Za-z0-9]{4,8})",
        r"(?i)one-time\s+code\s+is\s+([A-Za-z0-9]{4,8})",
        r"(?i)passcode\s+is\s+([A-Za-z0-9]{4,8})",
        r"(?i)security\s+code\s+is\s+([A-Za-z0-9]{4,8})",
        r"\b(\d{6})\b",  # fallback 6-digit
        r"\b(\d{4,8})\b",  # fallback any 4–8 digit
    ]

    def __init__(
        self,
        *,
        patterns: list[str] | None = None,
        event_bus: Any | None = None,
        ledger: Any | None = None,
        tier_gate: Any | None = None,
    ) -> None:
        super().__init__(event_bus=event_bus, ledger=ledger, tier_gate=tier_gate)
        self._patterns = patterns or list(self.DEFAULT_PATTERNS)

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        """Process a generic action dict.

        Expected shapes::

            {"method": "extract", "kwargs": {"text": "..."}}
            {"method": "process_email", "kwargs": {"email_data": {...}}}
            {"method": "process_sms", "kwargs": {"sms_data": {...}}}
        """
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "extract":
            return self.extract_code(kwargs.get("text", ""))
        if method == "process_email":
            return await self.process_email(kwargs.get("email_data", {}))
        if method == "process_sms":
            return await self.process_sms(kwargs.get("sms_data", {}))
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        """Extraction is pure CPU; no marginal cost."""
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 0

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy", "patterns_loaded": len(self._patterns)}

    # ------------------------------------------------------------------ #
    # Extraction logic
    # ------------------------------------------------------------------ #

    def extract_code(self, text: str) -> str | None:
        """Return the first verification code found in *text*, or None."""
        if not text:
            return None
        for pattern in self._patterns:
            match = re.search(pattern, text)
            if match:
                code = match.group(1) if match.groups() else match.group(0)
                return code
        return None

    async def process_email(self, email_data: dict[str, Any]) -> str | None:
        """Extract a code from an email dict and emit an event."""
        text = f"{email_data.get('subject', '')} {email_data.get('body', '')}"
        code = self.extract_code(text)
        if code:
            self._emit("limb.verification.extracted", {
                "code": code,
                "source": "email",
                "sender": email_data.get("from", "unknown"),
                "context": email_data.get("subject", ""),
            })
            logger.info("Extracted verification code from email: %s", code)
        return code

    async def process_sms(self, sms_data: dict[str, Any]) -> str | None:
        """Extract a code from an SMS dict and emit an event."""
        text = sms_data.get("body", "")
        code = self.extract_code(text)
        if code:
            self._emit("limb.verification.extracted", {
                "code": code,
                "source": "sms",
                "sender": sms_data.get("from", "unknown"),
                "context": text,
            })
            logger.info("Extracted verification code from SMS: %s", code)
        return code
