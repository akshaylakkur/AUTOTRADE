"""Async email client for human-in-the-loop approval proposals."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib

from auton.limbs.base_limb import BaseLimb
from auton.limbs.communications.queue import EmailQueue
from auton.limbs.communications.templates import render_proposal

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ActionProposal:
    """A proposal for a financial or deploy action requiring human approval."""

    action_type: str  # "trade", "deployment", "generic"
    what: str
    why: str
    risk: str
    expected_outcome: str
    urgency: str  # "low", "medium", "high", "critical"
    approval_token: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SMTPConfig:
    """SMTP connection parameters for the email client."""

    host: str
    port: int
    username: str
    password: str
    use_tls: bool = True
    from_address: str = "aeon@auton.local"


class EmailClient(BaseLimb):
    """SMTP client that sends action proposals for human approval.

    Failed sends are persisted to :class:`EmailQueue` and retried in the
    background with exponential backoff.
    """

    _MAX_RETRIES = 5
    _BASE_BACKOFF_SECONDS = 30

    def __init__(
        self,
        config: SMTPConfig,
        queue: EmailQueue | None = None,
        recipient: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._queue = queue or EmailQueue()
        self._recipient = recipient
        self._retry_task: asyncio.Task[Any] | None = None
        self._shutdown_event = asyncio.Event()

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        """Execute a generic action dict.

        Expected shapes::

            {"method": "send_proposal", "kwargs": {"proposal": ActionProposal}}
            {"method": "start_retry_worker"}
            {"method": "stop_retry_worker"}
        """
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "send_proposal":
            return await self.send_proposal(kwargs["proposal"])
        if method == "start_retry_worker":
            await self.start_retry_worker()
            return True
        if method == "stop_retry_worker":
            await self.stop_retry_worker()
            return True
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        """Email delivery has negligible marginal cost."""
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 0

    async def health_check(self) -> dict[str, Any]:
        try:
            await self._smtp_connect_test()
            return {"status": "healthy", "mode": "live"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def send_proposal(self, proposal: ActionProposal) -> bool:
        """Send an action proposal via email.

        Args:
            proposal: The action proposal to send.

        Returns:
            True if the email was accepted by the SMTP server, False otherwise.
        """
        if self._recipient is None:
            logger.error("EmailClient: no recipient configured")
            return False

        try:
            await self._send_email(
                to=self._recipient,
                subject=f"[AEON] Approval Required: {proposal.what}",
                proposal=proposal,
            )
            logger.info("EmailClient: proposal sent (%s)", proposal.approval_token)
            self._emit(
                "limb.communications.proposal_sent",
                {
                    "approval_token": proposal.approval_token,
                    "recipient": self._recipient,
                    "action_type": proposal.action_type,
                },
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "EmailClient: send failed (%s) -- enqueuing for retry",
                exc,
            )
            rendered = render_proposal(proposal)
            await self._queue.enqueue(
                recipient=self._recipient,
                subject=f"[AEON] Approval Required: {proposal.what}",
                text_body=rendered["text"],
                html_body=rendered["html"],
                proposal_token=proposal.approval_token,
            )
            self._emit(
                "limb.communications.proposal_queued",
                {
                    "approval_token": proposal.approval_token,
                    "recipient": self._recipient,
                    "action_type": proposal.action_type,
                },
            )
            return False

    async def start_retry_worker(self) -> None:
        """Start the background worker that retries failed emails."""
        if self._retry_task is None or self._retry_task.done():
            self._shutdown_event.clear()
            self._retry_task = asyncio.create_task(self._retry_loop())

    async def stop_retry_worker(self) -> None:
        """Stop the background retry worker."""
        self._shutdown_event.set()
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _send_email(
        self,
        *,
        to: str,
        subject: str,
        proposal: ActionProposal,
    ) -> None:
        rendered = render_proposal(proposal)
        msg = self._build_message(
            to=to,
            subject=subject,
            text_body=rendered["text"],
            html_body=rendered["html"],
        )
        await aiosmtplib.send(
            msg,
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
            start_tls=self._config.use_tls,
        )

    def _build_message(
        self,
        *,
        to: str,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._config.from_address
        msg["To"] = to
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        return msg

    async def _smtp_connect_test(self) -> None:
        """Perform a lightweight SMTP connect test."""
        await aiosmtplib.send(
            self._build_message(
                to=self._recipient or self._config.from_address,
                subject="AEON health check",
                text_body="ping",
                html_body="<p>ping</p>",
            ),
            hostname=self._config.host,
            port=self._config.port,
            username=self._config.username,
            password=self._config.password,
            start_tls=self._config.use_tls,
        )

    async def _retry_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                due = await self._queue.dequeue(batch_size=5)
                for email in due:
                    if email.retry_count >= self._MAX_RETRIES:
                        logger.error(
                            "EmailClient: max retries exceeded for token %s -- dropping",
                            email.proposal_token,
                        )
                        await self._queue.mark_sent(email.id)
                        self._emit(
                            "limb.communications.proposal_dropped",
                            {"approval_token": email.proposal_token},
                        )
                        continue

                    try:
                        msg = self._build_message(
                            to=email.recipient,
                            subject=email.subject,
                            text_body=email.text_body,
                            html_body=email.html_body,
                        )
                        await aiosmtplib.send(
                            msg,
                            hostname=self._config.host,
                            port=self._config.port,
                            username=self._config.username,
                            password=self._config.password,
                            start_tls=self._config.use_tls,
                        )
                        await self._queue.mark_sent(email.id)
                        logger.info(
                            "EmailClient: retry success for token %s",
                            email.proposal_token,
                        )
                        self._emit(
                            "limb.communications.proposal_sent",
                            {"approval_token": email.proposal_token},
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "EmailClient: retry %d failed for token %s (%s)",
                            email.retry_count + 1,
                            email.proposal_token,
                            exc,
                        )
                        backoff = self._BASE_BACKOFF_SECONDS * (2 ** email.retry_count)
                        next_retry = datetime.now(timezone.utc) + timedelta(seconds=backoff)
                        await self._queue.increment_retry(email.id, next_retry)

                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("EmailClient: unexpected error in retry loop")
                await asyncio.sleep(5.0)
