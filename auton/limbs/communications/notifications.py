"""Notification dispatcher for sending alerts via email and SMS."""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Any

import httpx

from auton.limbs.base_limb import BaseLimb

logger = logging.getLogger(__name__)


class NotificationDispatcher(BaseLimb):
    """Dispatch alerts via email (SMTP/SendGrid/SES) or SMS (Twilio).

    Priority mapping:
    - ``critical`` → SMS + Email
    - ``normal``   → Email
    - ``low``      → Email (batched)

    Credentials are sourced from environment variables unless provided explicitly.
    """

    def __init__(
        self,
        *,
        email_provider: str = "smtp",
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        sendgrid_api_key: str | None = None,
        ses_access_key: str | None = None,
        ses_secret_key: str | None = None,
        ses_region: str = "us-east-1",
        twilio_account_sid: str | None = None,
        twilio_auth_token: str | None = None,
        twilio_phone_number: str | None = None,
        default_email_recipient: str | None = None,
        default_sms_recipient: str | None = None,
        event_bus: Any | None = None,
        ledger: Any | None = None,
        tier_gate: Any | None = None,
    ) -> None:
        super().__init__(event_bus=event_bus, ledger=ledger, tier_gate=tier_gate)
        self._email_provider = email_provider.lower()

        # SMTP
        self._smtp_host = smtp_host or os.environ.get("AEON_SMTP_HOST", "smtp.gmail.com")
        self._smtp_port = smtp_port or int(os.environ.get("AEON_SMTP_PORT", "587"))
        self._smtp_user = smtp_user or os.environ.get("AEON_SMTP_USER", "")
        self._smtp_password = smtp_password or os.environ.get("AEON_SMTP_PASSWORD", "")

        # SendGrid
        self._sendgrid_api_key = sendgrid_api_key or os.environ.get("AEON_SENDGRID_API_KEY", "")

        # AWS SES
        self._ses_access_key = ses_access_key or os.environ.get("AEON_SES_ACCESS_KEY", "")
        self._ses_secret_key = ses_secret_key or os.environ.get("AEON_SES_SECRET_KEY", "")
        self._ses_region = ses_region or os.environ.get("AEON_SES_REGION", "us-east-1")

        # Twilio
        self._twilio_account_sid = twilio_account_sid or os.environ.get("AEON_TWILIO_ACCOUNT_SID", "")
        self._twilio_auth_token = twilio_auth_token or os.environ.get("AEON_TWILIO_AUTH_TOKEN", "")
        self._twilio_phone_number = twilio_phone_number or os.environ.get("AEON_TWILIO_PHONE_NUMBER", "")

        # Defaults
        self._default_email_recipient = default_email_recipient or os.environ.get("AEON_DEFAULT_EMAIL_RECIPIENT", "")
        self._default_sms_recipient = default_sms_recipient or os.environ.get("AEON_DEFAULT_SMS_RECIPIENT", "")

        self._client = httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        """Dispatch a generic action dict.

        Expected shape::

            {
                "method": "dispatch",
                "kwargs": {
                    "alert_type": "low_balance",
                    "message": "Balance below threshold",
                    "priority": "critical",
                    "recipients": {"email": "...", "sms": "..."}
                }
            }
        """
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "dispatch":
            return await self.dispatch(**kwargs)
        if method == "send_email":
            return await self.send_email(**kwargs)
        if method == "send_sms":
            return await self.send_sms(**kwargs)
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        """Return estimated cost in USD for an action."""
        method = action.get("method")
        if method in ("send_sms", "dispatch"):
            # Twilio SMS ~ $0.0075
            return 0.0075
        if method == "send_email":
            # Most providers charge ~$0.0001 or nothing for first tier
            return 0.0001
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 0

    async def health_check(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        # SMTP
        try:
            if self._smtp_host and self._smtp_user:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: smtplib.SMTP(self._smtp_host, self._smtp_port).quit(),
                )
                results["smtp"] = "healthy"
            else:
                results["smtp"] = "not_configured"
        except Exception as exc:  # noqa: BLE001
            results["smtp"] = f"unhealthy: {exc}"

        # Twilio
        try:
            if self._twilio_account_sid:
                r = await self._client.get(
                    f"https://api.twilio.com/2010-04-01/Accounts/{self._twilio_account_sid}.json",
                    auth=(self._twilio_account_sid, self._twilio_auth_token),
                )
                results["twilio"] = "healthy" if r.status_code == 200 else f"degraded: {r.status_code}"
            else:
                results["twilio"] = "not_configured"
        except Exception as exc:  # noqa: BLE001
            results["twilio"] = f"unhealthy: {exc}"

        results["status"] = "healthy" if all(v.startswith("healthy") or v == "not_configured" for v in results.values()) else "degraded"
        return results

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def dispatch(
        self,
        alert_type: str,
        message: str,
        priority: str = "normal",
        recipients: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send an alert via the correct channel(s) based on priority.

        Args:
            alert_type: classifier, e.g. ``low_balance``, ``error``, ``opportunity``.
            message: human-readable alert body.
            priority: ``critical``, ``normal``, or ``low``.
            recipients: optional dict with ``email`` and/or ``sms`` keys.
        """
        recipients = recipients or {}
        email_recipient = recipients.get("email") or self._default_email_recipient
        sms_recipient = recipients.get("sms") or self._default_sms_recipient
        result: dict[str, Any] = {"alert_type": alert_type, "priority": priority, "sent": {}}

        if priority == "critical":
            if sms_recipient:
                sms_result = await self.send_sms(sms_recipient, f"[CRITICAL] {message}")
                result["sent"]["sms"] = sms_result
            if email_recipient:
                email_result = await self.send_email(
                    email_recipient,
                    f"[CRITICAL] {alert_type}",
                    message,
                )
                result["sent"]["email"] = email_result
        elif priority == "normal":
            if email_recipient:
                email_result = await self.send_email(
                    email_recipient,
                    f"[{alert_type.upper()}] Alert",
                    message,
                )
                result["sent"]["email"] = email_result
        else:
            # low priority — just log; could be batched later
            logger.info("[LOW] %s: %s", alert_type, message)
            result["sent"]["logged"] = True

        self._emit("limb.notification.sent", {
            "alert_type": alert_type,
            "priority": priority,
            "channels": list(result["sent"].keys()),
        })

        await self._charge(self._estimate_cost(result), f"notification:{alert_type}")
        return result

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Send an email via the configured provider."""
        provider = (provider or self._email_provider).lower()
        if provider == "smtp":
            return await self._send_via_smtp(to, subject, body)
        if provider == "sendgrid":
            return await self._send_via_sendgrid(to, subject, body)
        if provider == "ses":
            return await self._send_via_ses(to, subject, body)
        raise ValueError(f"Unsupported email provider: {provider}")

    async def send_sms(self, to: str, body: str) -> dict[str, Any]:
        """Send an SMS via Twilio."""
        if not self._twilio_account_sid or not self._twilio_auth_token:
            logger.warning("Twilio not configured; SMS not sent.")
            return {"status": "not_configured", "to": to}

        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/{self._twilio_account_sid}"
            "/Messages.json"
        )
        data = {
            "From": self._twilio_phone_number,
            "To": to,
            "Body": body,
        }
        r = await self._client.post(url, data=data, auth=(self._twilio_account_sid, self._twilio_auth_token))
        if r.status_code >= 400:
            logger.error("Twilio SMS failed: %s %s", r.status_code, r.text)
            return {"status": "failed", "code": r.status_code, "error": r.text, "to": to}
        resp = r.json()
        self._emit("limb.sms.sent", {"to": to, "sid": resp.get("sid")})
        return {"status": "sent", "sid": resp.get("sid"), "to": to}

    # ------------------------------------------------------------------ #
    # Internal send implementations
    # ------------------------------------------------------------------ #

    async def _send_via_smtp(self, to: str, subject: str, body: str) -> dict[str, Any]:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self._smtp_user
        msg["To"] = to

        def _send() -> None:
            with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
                server.starttls()
                server.login(self._smtp_user, self._smtp_password)
                server.send_message(msg)

        await asyncio.get_event_loop().run_in_executor(None, _send)
        self._emit("limb.email.sent", {"provider": "smtp", "to": to, "subject": subject})
        return {"status": "sent", "provider": "smtp", "to": to}

    async def _send_via_sendgrid(self, to: str, subject: str, body: str) -> dict[str, Any]:
        if not self._sendgrid_api_key:
            raise RuntimeError("SendGrid API key is not configured")
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": self._smtp_user or "aeon@auton.ai"},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        r = await self._client.post(
            "https://api.sendgrid.com/v3/mail/send",
            json=payload,
            headers={"Authorization": f"Bearer {self._sendgrid_api_key}"},
        )
        if r.status_code >= 400:
            logger.error("SendGrid failed: %s %s", r.status_code, r.text)
            return {"status": "failed", "code": r.status_code, "error": r.text, "to": to}
        self._emit("limb.email.sent", {"provider": "sendgrid", "to": to, "subject": subject})
        return {"status": "sent", "provider": "sendgrid", "to": to}

    async def _send_via_ses(self, to: str, subject: str, body: str) -> dict[str, Any]:
        if not self._ses_access_key or not self._ses_secret_key:
            raise RuntimeError("AWS SES credentials are not configured")
        # Use the SES v2 SendEmail API via HTTPS
        endpoint = f"https://email.{self._ses_region}.amazonaws.com/v2/email/outbound-emails"
        payload = {
            "Content": {"Simple": {"Subject": {"Data": subject}, "Body": {"Text": {"Data": body}}}},
            "Destination": {"ToAddresses": [to]},
            "FromEmailAddress": self._smtp_user or "aeon@auton.ai",
        }
        # AWS SigV4 signing is complex; for the skeleton we send unsigned or rely on IAM role.
        # In production this should use aiobotocore or proper SigV4 signing.
        r = await self._client.post(
            endpoint,
            json=payload,
            auth=(self._ses_access_key, self._ses_secret_key),
        )
        if r.status_code >= 400:
            logger.error("SES failed: %s %s", r.status_code, r.text)
            return {"status": "failed", "code": r.status_code, "error": r.text, "to": to}
        self._emit("limb.email.sent", {"provider": "ses", "to": to, "subject": subject})
        return {"status": "sent", "provider": "ses", "to": to}

    # ------------------------------------------------------------------ #
    # Utils
    # ------------------------------------------------------------------ #

    def _estimate_cost(self, result: dict[str, Any]) -> float:
        channels = result.get("sent", {})
        cost = 0.0
        if "sms" in channels:
            cost += 0.0075
        if "email" in channels:
            cost += 0.0001
        return cost

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> NotificationDispatcher:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
