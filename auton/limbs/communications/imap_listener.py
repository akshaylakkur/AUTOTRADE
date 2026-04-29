"""Async IMAP email listener for Project AEON.

Polls an IMAP inbox periodically for new emails and emits typed events when
messages or verification codes are received, and routes APPROVE/REJECT
responses to the HumanGateway.
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import os
import re
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any

from auton.core.events import MessageReceived, VerificationCodeReceived

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_APPROVE_RE = re.compile(
    r"\bAPPROVE\s+(PROP-\d{14}-[a-f0-9]{8})\b", re.IGNORECASE
)
_REJECT_RE = re.compile(
    r"\bREJECT\s+(PROP-\d{14}-[a-f0-9]{8})\b", re.IGNORECASE
)

_CODE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(ptn, re.IGNORECASE)
    for ptn in [
        r"verification\s+code:\s*(\d{4,8})",
        r"verification\s+code\s+is\s+(\d{4,8})",
        r"code:\s*(\d{4,8})",
        r"code\s+is\s+(\d{4,8})",
        r"your\s+code\s+is\s+(\d{4,8})",
        r"otp:\s*(\d{4,8})",
        r"otp\s+is\s+(\d{4,8})",
        r"one-time\s+code:\s*(\d{4,8})",
        r"passcode:\s*(\d{4,8})",
        r"security\s+code:\s*(\d{4,8})",
        r"\b(\d{6})\b",  # fallback: standalone 6-digit code
    ]
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_header_value(value: str | bytes | None) -> str:
    """Decode a potentially RFC-2047 encoded header value into a plain string."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    parts: list[str] = []
    for fragment, charset in decode_header(value):
        if isinstance(fragment, bytes):
            charset = charset or "utf-8"
            try:
                fragment = fragment.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                fragment = fragment.decode("utf-8", errors="replace")
        parts.append(str(fragment) if fragment else "")
    return "".join(parts)


def _extract_plain_text(msg: Message) -> str:
    """Walk a MIME message tree and return the concatenated text/plain parts."""
    texts: list[str] = []

    def _walk(part: Message) -> None:
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" in disposition:
            return
        if content_type == "text/plain":
            payload = part.get_payload(decode=True)
            if payload is None:
                return
            charset = part.get_content_charset() or "utf-8"
            try:
                texts.append(payload.decode(charset, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                texts.append(payload.decode("utf-8", errors="replace"))
        elif content_type.startswith("multipart/"):
            for sub in part.get_payload() if isinstance(part.get_payload(), list) else []:
                _walk(sub)

    _walk(msg)
    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IMAPConfig:
    """Connection parameters for an IMAP mailbox.

    All attributes default to values from environment variables so that
    ``IMAPConfig()`` "just works" in a deployed container.
    """

    host: str = field(
        default_factory=lambda: os.environ.get("AEON_IMAP_HOST", "")
    )
    port: int = field(
        default_factory=lambda: int(os.environ.get("AEON_IMAP_PORT", "993"))
    )
    username: str = field(
        default_factory=lambda: os.environ.get("AEON_IMAP_USERNAME", "")
    )
    password: str = field(
        default_factory=lambda: os.environ.get("AEON_IMAP_PASSWORD", "")
    )
    mailbox: str = "INBOX"
    ssl_context: ssl.SSLContext | None = None  # created lazily if None


# ---------------------------------------------------------------------------
# IMAP Listener
# ---------------------------------------------------------------------------


class IMAPListener:
    """Async IMAP listener that polls a mailbox for new messages.

    For each **unseen** email the listener:

    * Emits a typed ``MessageReceived`` event on the EventBus.
    * Scans the body for ``APPROVE PROP-XXXX`` / ``REJECT PROP-XXXX``
      commands and forwards them to :class:`~auton.limbs.human_gateway.HumanGateway`.
    * Scans for verification codes (e.g. ``code: 123456``) and emits
      a typed ``VerificationCodeReceived`` event.
    * Marks the message as seen so it is only processed once.

    Parameters
    ----------
    config:
        IMAP connection parameters.  If omitted, values are read from
        ``AEON_IMAP_HOST``, ``AEON_IMAP_PORT``, ``AEON_IMAP_USERNAME``,
        and ``AEON_IMAP_PASSWORD`` environment variables.
    event_bus:
        :class:`~auton.core.event_bus.EventBus` instance for typed events.
    human_gateway:
        :class:`~auton.limbs.human_gateway.HumanGateway` instance whose
        ``approve()`` / ``reject()`` methods are called for parsed commands.
    poll_interval_seconds:
        Seconds to sleep between mailbox scans when no messages were found.
        When messages **are** found the next scan starts immediately to
        drain the mailbox quickly.
    connect_timeout_seconds:
        Timeout for the initial TCP/SSL handshake.
    """

    def __init__(
        self,
        config: IMAPConfig | None = None,
        *,
        event_bus: Any | None = None,
        human_gateway: Any | None = None,
        poll_interval_seconds: float = 30.0,
        connect_timeout_seconds: float = 15.0,
    ) -> None:
        self._config = config or IMAPConfig()
        self._event_bus = event_bus
        self._human_gateway = human_gateway
        self._poll_interval = poll_interval_seconds
        self._connect_timeout = connect_timeout_seconds

        self._task: asyncio.Task[Any] | None = None
        self._stop_event = asyncio.Event()
        self._running = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def running(self) -> bool:
        """Return ``True`` while the background poll loop is active."""
        return self._running

    async def start(self) -> None:
        """Start the background IMAP polling task.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._running:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop(), name="imap-listener")
        self._running = True
        logger.info(
            "IMAPListener started (host=%s, port=%d, interval=%.1fs)",
            self._config.host or "<unset>",
            self._config.port,
            self._poll_interval,
        )

    async def stop(self) -> None:
        """Gracefully stop the background polling task.

        The current poll cycle (if any) is allowed to finish before the
        task exits.  Safe to call when already stopped.
        """
        if not self._running:
            return
        self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._running = False
        logger.info("IMAPListener stopped")

    # ------------------------------------------------------------------ #
    # Poll loop
    # ------------------------------------------------------------------ #

    async def _poll_loop(self) -> None:
        """Main loop: connect, scan for unseen mail, repeat."""
        while not self._stop_event.is_set():
            imap_conn: imaplib.IMAP4_SSL | None = None
            try:
                imap_conn = await self._connect()
                await self._scan_unseen(imap_conn)
            except (imaplib.IMAP4.error, ssl.SSLError, socket.gaierror,
                    ConnectionRefusedError, TimeoutError, OSError) as exc:
                logger.warning("IMAPListener: connection error — %s", exc)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("IMAPListener: unexpected error in poll loop")
            finally:
                if imap_conn is not None:
                    self._safe_logout(imap_conn)

            # Wait for the next poll cycle (or a stop signal)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass  # normal — polling interval elapsed

    async def _connect(self) -> imaplib.IMAP4_SSL:
        """Open an authenticated IMAP SSL connection (run in a thread)."""
        loop = asyncio.get_running_loop()

        def _connect_sync() -> imaplib.IMAP4_SSL:
            ssl_ctx = self._config.ssl_context or ssl.create_default_context()
            conn = imaplib.IMAP4_SSL(
                host=self._config.host,
                port=self._config.port,
                ssl_context=ssl_ctx,
                timeout=self._connect_timeout,
            )
            conn.login(self._config.username, self._config.password)
            logger.debug(
                "IMAPListener: connected to %s:%d as %s",
                self._config.host,
                self._config.port,
                self._config.username,
            )
            return conn

        return await loop.run_in_executor(None, _connect_sync)

    # ------------------------------------------------------------------ #
    # Mailbox scan
    # ------------------------------------------------------------------ #

    async def _scan_unseen(self, conn: imaplib.IMAP4_SSL) -> None:
        """Fetch all unseen messages, process them, and mark them read."""
        loop = asyncio.get_running_loop()

        def _select() -> int:
            status, data = conn.select(self._config.mailbox, readonly=False)
            if status != "OK":
                raise imaplib.IMAP4.error(f"SELECT failed: {status!r}")
            return int(data[0]) if data else 0

        total = await loop.run_in_executor(None, _select)
        if total == 0:
            return

        def _search() -> list[bytes]:
            status, data = conn.search(None, "UNSEEN")
            if status != "OK":
                raise imaplib.IMAP4.error(f"SEARCH UNSEEN failed: {status!r}")
            # data is like [b"1 2 3 4"] — split into individual IDs
            if not data or not data[0]:
                return []
            return data[0].split()

        uids = await loop.run_in_executor(None, _search)
        if not uids:
            return

        logger.info(
            "IMAPListener: found %d unseen message(s) out of %d total",
            len(uids),
            total,
        )

        for uid in uids:
            if self._stop_event.is_set():
                break
            try:
                await self._fetch_and_process(conn, uid)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "IMAPListener: error processing message %s", uid.decode()
                )

    async def _fetch_and_process(
        self, conn: imaplib.IMAP4_SSL, uid: bytes
    ) -> None:
        """Fetch a single message by UID, process it, and mark it seen."""
        loop = asyncio.get_running_loop()

        def _fetch() -> tuple[bytes, bytes, str, str, str, str, str]:
            status, data = conn.fetch(uid, "(BODY.PEEK[] FLAGS)")
            if status != "OK":
                raise imaplib.IMAP4.error(f"FETCH failed: {status!r}")

            raw_bytes: bytes | None = None
            flags: bytes = b""
            # data is structured as alternating flag/item pairs
            for item in data:
                if isinstance(item, bytes):
                    continue
                # item can be a tuple like (b"1 (FLAGS ...)", b"<raw email>")
                if isinstance(item, tuple):
                    for part in item:
                        if isinstance(part, bytes):
                            decoded_part = part.decode("ascii", errors="ignore")
                            if "FLAGS" in decoded_part:
                                flags = part
                            elif len(part) > 100:
                                raw_bytes = part

            if raw_bytes is None:
                # fallback: try parsing without FLAGS
                status2, data2 = conn.fetch(uid, "(BODY.PEEK[])")
                if status2 != "OK":
                    raise imaplib.IMAP4.error(f"FETCH retry failed: {status2!r}")
                for item in data2:
                    if isinstance(item, tuple):
                        for part in item:
                            if isinstance(part, bytes) and len(part) > 100:
                                raw_bytes = part
                                break

            if raw_bytes is None:
                raise imaplib.IMAP4.error("No message body in FETCH response")

            msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

            subject = _decode_header_value(msg["Subject"])
            sender = _decode_header_value(msg["From"])
            date_raw = _decode_header_value(msg["Date"])
            message_id = _decode_header_value(msg["Message-ID"])
            body = _extract_plain_text(msg)

            return raw_bytes, flags, subject, sender, date_raw, message_id, body

        raw_bytes, flags, subject, sender, date_raw, message_id, body = (
            await loop.run_in_executor(None, _fetch)
        )

        # ------------------------------------------------------------------ #
        # Process the message contents
        # ------------------------------------------------------------------ #

        # Always emit a MessageReceived event
        msg_event = MessageReceived(
            source="email",
            sender=sender,
            subject=subject,
            body=body,
            raw_payload={
                "date": date_raw,
                "message_id": message_id,
                "uid": uid.decode("ascii"),
            },
        )
        await self._publish(MessageReceived, msg_event)

        # Look for APPROVE / REJECT commands
        self._handle_approval_reject(re.sub(r"\s+", " ", body))

        # Look for verification codes
        code = self._extract_code(body)
        if code:
            vc_event = VerificationCodeReceived(
                source="email",
                code=code,
            )
            await self._publish(VerificationCodeReceived, vc_event)
            logger.info(
                "IMAPListener: verification code %s from %s", code, sender
            )

        # Mark the message as seen
        def _mark_seen() -> None:
            conn.store(uid, "+FLAGS", "\\Seen")

        await loop.run_in_executor(None, _mark_seen)

    # ------------------------------------------------------------------ #
    # Content scanners
    # ------------------------------------------------------------------ #

    def _handle_approval_reject(self, body_text: str) -> None:
        """Scan *body_text* for APPROVE/REJECT directives and route them."""
        if self._human_gateway is None:
            return

        for match in _APPROVE_RE.finditer(body_text):
            proposal_id = match.group(1)
            ok = self._human_gateway.approve(proposal_id)
            if ok:
                logger.info(
                    "IMAPListener: approved proposal %s via email", proposal_id
                )
            else:
                logger.warning(
                    "IMAPListener: APPROVE for unknown/expired proposal %s",
                    proposal_id,
                )

        for match in _REJECT_RE.finditer(body_text):
            proposal_id = match.group(1)
            ok = self._human_gateway.reject(proposal_id)
            if ok:
                logger.info(
                    "IMAPListener: rejected proposal %s via email", proposal_id
                )
            else:
                logger.warning(
                    "IMAPListener: REJECT for unknown/expired proposal %s",
                    proposal_id,
                )

    @staticmethod
    def _extract_code(text: str) -> str | None:
        """Return the first verification code found in *text*, or None."""
        if not text:
            return None
        # Normalize whitespace to simplify matching
        cleaned = re.sub(r"\s+", " ", text)
        for pattern in _CODE_PATTERNS:
            m = pattern.search(cleaned)
            if m:
                return m.group(1) if m.groups() else m.group(0)
        return None

    # ------------------------------------------------------------------ #
    # Event bus helper
    # ------------------------------------------------------------------ #

    async def _publish(self, event_type: type, payload: Any) -> None:
        """Publish a typed event if an EventBus is configured."""
        if self._event_bus is None:
            return
        try:
            if hasattr(self._event_bus, "publish"):
                await self._event_bus.publish(event_type, payload)
        except Exception:  # noqa: BLE001
            logger.exception(
                "IMAPListener: failed to publish event %s", event_type.__name__
            )

    # ------------------------------------------------------------------ #
    # Cleanup helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_logout(conn: imaplib.IMAP4_SSL) -> None:
        """Attempt logout but never raise."""
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
