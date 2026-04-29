"""Twilio SMS client for receiving text messages (inbound)."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from auton.senses.base_connector import BaseConnector

logger = logging.getLogger(__name__)


class SmsClient(BaseConnector):
    """Async Twilio SMS client for fetching received messages.

    Credentials are read from environment variables unless overridden:
    - ``AEON_TWILIO_ACCOUNT_SID``
    - ``AEON_TWILIO_AUTH_TOKEN``
    - ``AEON_TWILIO_PHONE_NUMBER``
    """

    _TWILIO_API_VERSION = "2010-04-01"

    def __init__(
        self,
        *,
        account_sid: str | None = None,
        auth_token: str | None = None,
        phone_number: str | None = None,
        event_bus: Any | None = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self._account_sid = account_sid or os.environ.get("AEON_TWILIO_ACCOUNT_SID", "")
        self._auth_token = auth_token or os.environ.get("AEON_TWILIO_AUTH_TOKEN", "")
        self._phone_number = phone_number or os.environ.get("AEON_TWILIO_PHONE_NUMBER", "")
        self._client = httpx.AsyncClient(
            auth=(self._account_sid, self._auth_token),
            timeout=30.0,
        )

    # ------------------------------------------------------------------ #
    # BaseConnector interface
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        """Verify credentials with a lightweight API call."""
        self._connected = True
        logger.info("SmsClient connected")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()
        self._connected = False
        logger.info("SmsClient disconnected")

    async def fetch_data(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch messages according to *params*.

        Supported params:
        - ``to`` (str): filter by recipient number.
        - ``from_`` (str): filter by sender number.
        - ``limit`` (int): max messages, default 10.
        """
        to = params.get("to")
        from_ = params.get("from_")
        limit = params.get("limit", 10)
        return await self._fetch_messages(to=to, from_=from_, limit=limit)

    def get_subscription_cost(self) -> dict[str, float]:
        # Twilio charges per message; receiving SMS is usually free or very low
        return {"monthly": 1.0, "daily": 0.03}

    def is_available(self, tier: int) -> bool:
        return tier >= 0

    # ------------------------------------------------------------------ #
    # Public helpers
    # ------------------------------------------------------------------ #

    async def fetch_messages(
        self,
        to: str | None = None,
        from_: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return a list of SMS message dicts."""
        result = await self._fetch_messages(to=to, from_=from_, limit=limit)
        return result.get("messages", [])

    async def fetch_recent_inbound(self, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch recent inbound messages to the configured phone number."""
        return await self.fetch_messages(to=self._phone_number, limit=limit)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _fetch_messages(
        self,
        to: str | None = None,
        from_: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not self._account_sid:
            raise RuntimeError("Twilio account_sid is not configured")

        url = f"/{self._TWILIO_API_VERSION}/Accounts/{self._account_sid}/Messages.json"
        query: dict[str, Any] = {"PageSize": limit}
        if to:
            query["To"] = to
        if from_:
            query["From"] = from_

        r = await self._client.get(url, params=query)
        r.raise_for_status()
        data = r.json()
        messages = data.get("messages", [])
        normalized = [
            {
                "sid": m.get("sid"),
                "from": m.get("from"),
                "to": m.get("to"),
                "body": m.get("body", ""),
                "status": m.get("status"),
                "direction": m.get("direction"),
                "date_sent": m.get("date_sent"),
                "price": m.get("price"),
                "raw": m,
            }
            for m in messages
        ]
        payload = {"messages": normalized, "count": len(normalized)}
        await self._emit_data(payload)
        return payload

    async def close(self) -> None:
        await self.disconnect()

    async def __aenter__(self) -> SmsClient:
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
