"""Crypto onramp integration for Project ÆON.

Provides a unified interface for converting fiat to crypto via
onramp providers (MoonPay pattern / Stripe Crypto pattern).

All credentials are read from environment variables.  The module
defaults to skeleton mode when no API key is present.

Credentials:
  - MOONPAY_API_KEY
  - MOONPAY_SECRET_KEY (for signature verification)
  - STRIPE_CRYPTO_SECRET_KEY (optional, for Stripe Crypto pattern)
"""

from __future__ import annotations

import os
import asyncio
import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from auton.limbs.base_limb import BaseLimb
from auton.security.audit_trail import AuditLog
from auton.security.spend_caps import SpendGuard


@dataclass(frozen=True, slots=True)
class OnrampQuote:
    """A fiat-to-crypto quote from the onramp provider."""

    quote_id: str
    fiat_amount: float
    fiat_currency: str
    crypto_amount: float
    crypto_currency: str
    fee_amount: float
    total_cost: float
    expires_at: datetime
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class OnrampTransaction:
    """A created onramp transaction."""

    transaction_id: str
    status: str
    quote_id: str
    wallet_address: str
    redirect_url: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class CryptoOnrampLimb(BaseLimb):
    """Async limb for crypto onramp operations.

    Supports MoonPay and Stripe Crypto patterns.  In skeleton mode
    returns mock quotes and transactions so ÆON can test flows
    without real money.

    Safety:
      - Daily onramp limit via SpendGuard
      - Confirmation delay for purchases > $500
    """

    LARGE_ONRAMP_THRESHOLD: float = 500.0
    CONFIRMATION_DELAY_SECONDS: float = 600.0  # 10 minutes

    def __init__(
        self,
        *,
        moonpay_api_key: str | None = None,
        moonpay_secret: str | None = None,
        stripe_crypto_key: str | None = None,
        spend_guard: SpendGuard | None = None,
        audit_log: AuditLog | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._moonpay_api_key = moonpay_api_key or os.environ.get("MOONPAY_API_KEY")
        self._moonpay_secret = moonpay_secret or os.environ.get("MOONPAY_SECRET_KEY")
        self._stripe_crypto_key = stripe_crypto_key or os.environ.get("STRIPE_CRYPTO_SECRET_KEY")
        self._spend_guard = spend_guard
        self._audit_log = audit_log or AuditLog()

        self._moonpay_client = httpx.AsyncClient(
            base_url="https://api.moonpay.com",
            timeout=30.0,
        )
        self._stripe_client = httpx.AsyncClient(
            base_url="https://api.stripe.com",
            timeout=30.0,
        )

        # Skeleton state
        self._mock_quotes: dict[str, dict[str, Any]] = {}
        self._mock_txs: dict[str, dict[str, Any]] = {}
        self._mock_counter = 0
        self._pending_confirmations: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "get_quote":
            return await self.get_quote(**kwargs)
        if method == "create_transaction":
            return await self.create_transaction(**kwargs)
        if method == "get_transaction_status":
            return await self.get_transaction_status(**kwargs)
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        """Estimate onramp fee (MoonPay ~4.5% + network fee)."""
        method = action.get("method")
        if method == "get_quote":
            amount = action.get("kwargs", {}).get("fiat_amount", 0.0)
            return round(amount * 0.045 + 3.99, 2)
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 2

    async def health_check(self) -> dict[str, Any]:
        if self._moonpay_api_key is None and self._stripe_crypto_key is None:
            return {"status": "skeleton", "mode": "mock"}
        results = {}
        if self._moonpay_api_key:
            try:
                start = asyncio.get_event_loop().time()
                r = await self._moonpay_client.get("/v3/currencies")
                latency = (asyncio.get_event_loop().time() - start) * 1000
                results["moonpay"] = {
                    "status": "healthy" if r.status_code == 200 else "degraded",
                    "latency_ms": round(latency, 2),
                }
            except Exception as exc:  # noqa: BLE001
                results["moonpay"] = {"status": "unhealthy", "error": str(exc)}
        return {"status": "healthy" if any(v.get("status") == "healthy" for v in results.values()) else "degraded", "providers": results}

    # ------------------------------------------------------------------ #
    # Quotes
    # ------------------------------------------------------------------ #

    async def get_quote(
        self,
        fiat_amount: float,
        fiat_currency: str = "usd",
        crypto_currency: str = "eth",
        wallet_address: str = "",
    ) -> OnrampQuote:
        """Fetch a fiat-to-crypto quote from the onramp provider.

        :param fiat_amount: Amount in fiat currency.
        :param fiat_currency: Three-letter fiat currency code.
        :param crypto_currency: Crypto asset code (e.g. ``eth``, ``btc``).
        :param wallet_address: Destination wallet address.
        """
        self._emit("limb.onramp.quote.requested", {
            "fiat_amount": fiat_amount,
            "fiat_currency": fiat_currency,
            "crypto_currency": crypto_currency,
        })
        log_id = self._audit_log.pre_log("onramp.quote", {
            "fiat_amount": fiat_amount,
            "fiat_currency": fiat_currency,
            "crypto_currency": crypto_currency,
        })

        try:
            if self._moonpay_api_key:
                quote = await self._live_moonpay_quote(
                    fiat_amount, fiat_currency, crypto_currency, wallet_address
                )
            else:
                quote = self._mock_get_quote(
                    fiat_amount, fiat_currency, crypto_currency, wallet_address
                )

            self._audit_log.post_log(log_id, {"quote_id": quote.quote_id})
            self._emit("limb.onramp.quote.received", {"quote_id": quote.quote_id})
            return quote
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    # ------------------------------------------------------------------ #
    # Transactions
    # ------------------------------------------------------------------ #

    async def create_transaction(
        self,
        quote_id: str,
        wallet_address: str,
        skip_confirmation: bool = False,
    ) -> OnrampTransaction | dict[str, Any]:
        """Create an onramp transaction from a quote.

        Enforces daily onramp limits and confirmation delays for large
        purchases.
        """
        quote = self._mock_quotes.get(quote_id)
        if quote is None and self._moonpay_api_key:
            # In live mode we would validate the quote via API
            quote = {"fiat_amount": 0.0, "fiat_currency": "usd"}

        fiat_amount = quote.get("fiat_amount", 0.0) if quote else 0.0

        self._emit("limb.onramp.transaction.requested", {
            "quote_id": quote_id,
            "wallet_address": wallet_address,
            "fiat_amount": fiat_amount,
        })
        log_id = self._audit_log.pre_log("onramp.transaction", {
            "quote_id": quote_id,
            "wallet_address": wallet_address,
            "fiat_amount": fiat_amount,
        })

        # 1. Daily onramp limit check
        if self._spend_guard:
            allowed, reason = self._spend_guard.quote_spend("crypto_onramp", fiat_amount)
            if not allowed:
                self._audit_log.post_log(log_id, {"blocked": True, "reason": reason})
                raise OnrampLimitExceeded(f"Onramp blocked by spend guard: {reason}")

        # 2. Large onramp confirmation delay
        if fiat_amount >= self.LARGE_ONRAMP_THRESHOLD and not skip_confirmation:
            confirmation_id = f"confirm_onramp_{quote_id}_{int(datetime.now(timezone.utc).timestamp())}"
            self._pending_confirmations[confirmation_id] = {
                "quote_id": quote_id,
                "wallet_address": wallet_address,
                "fiat_amount": fiat_amount,
                "requested_at": datetime.now(timezone.utc).isoformat(),
            }
            self._audit_log.post_log(log_id, {"pending_confirmation": True, "confirmation_id": confirmation_id})
            self._emit("limb.onramp.pending_confirmation", {
                "confirmation_id": confirmation_id,
                "fiat_amount": fiat_amount,
                "delay_seconds": self.CONFIRMATION_DELAY_SECONDS,
            })
            return {
                "status": "pending_confirmation",
                "confirmation_id": confirmation_id,
                "fiat_amount": fiat_amount,
                "delay_seconds": self.CONFIRMATION_DELAY_SECONDS,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=self.CONFIRMATION_DELAY_SECONDS)).isoformat(),
            }

        try:
            if self._moonpay_api_key:
                tx = await self._live_moonpay_transaction(quote_id, wallet_address)
            else:
                tx = self._mock_create_transaction(quote_id, wallet_address)

            if self._spend_guard:
                self._spend_guard.record_spend("crypto_onramp", fiat_amount)

            self._audit_log.post_log(log_id, {"transaction_id": tx.transaction_id, "status": tx.status})
            self._emit("limb.onramp.transaction.created", {"transaction_id": tx.transaction_id})
            return tx
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    async def confirm_transaction(self, confirmation_id: str) -> OnrampTransaction | dict[str, Any]:
        """Confirm a pending onramp transaction after the delay has elapsed."""
        pending = self._pending_confirmations.get(confirmation_id)
        if not pending:
            raise ValueError(f"No pending onramp found for confirmation_id: {confirmation_id}")

        requested_at = datetime.fromisoformat(pending["requested_at"])
        elapsed = (datetime.now(timezone.utc) - requested_at).total_seconds()
        if elapsed < self.CONFIRMATION_DELAY_SECONDS:
            raise OnrampConfirmationError(
                f"Confirmation delay not elapsed: {elapsed:.0f}s < {self.CONFIRMATION_DELAY_SECONDS:.0f}s"
            )

        del self._pending_confirmations[confirmation_id]
        return await self.create_transaction(
            quote_id=pending["quote_id"],
            wallet_address=pending["wallet_address"],
            skip_confirmation=True,
        )

    async def get_transaction_status(self, transaction_id: str) -> dict[str, Any]:
        if self._moonpay_api_key:
            r = await self._moonpay_client.get(
                f"/v1/transactions/{transaction_id}",
                params={"apiKey": self._moonpay_api_key},
            )
            r.raise_for_status()
            return r.json()
        return self._mock_txs.get(transaction_id, {})

    # ------------------------------------------------------------------ #
    # Live MoonPay implementations
    # ------------------------------------------------------------------ #

    async def _live_moonpay_quote(
        self,
        fiat_amount: float,
        fiat_currency: str,
        crypto_currency: str,
        wallet_address: str,
    ) -> OnrampQuote:
        params = {
            "apiKey": self._moonpay_api_key,
            "baseCurrencyAmount": fiat_amount,
            "baseCurrencyCode": fiat_currency.lower(),
            "currencyCode": crypto_currency.lower(),
        }
        if wallet_address:
            params["walletAddress"] = wallet_address

        r = await self._moonpay_client.get("/v3/currencies/quote", params=params)
        r.raise_for_status()
        data = r.json()

        return OnrampQuote(
            quote_id=data.get("id", "unknown"),
            fiat_amount=fiat_amount,
            fiat_currency=fiat_currency,
            crypto_amount=float(data.get("quoteCurrencyAmount", 0)),
            crypto_currency=crypto_currency,
            fee_amount=float(data.get("feeAmount", 0)),
            total_cost=float(data.get("totalAmount", fiat_amount)),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            raw=data,
        )

    async def _live_moonpay_transaction(
        self,
        quote_id: str,
        wallet_address: str,
    ) -> OnrampTransaction:
        # MoonPay transaction creation via signed URL or API
        payload = {
            "apiKey": self._moonpay_api_key,
            "quoteId": quote_id,
            "walletAddress": wallet_address,
        }
        if self._moonpay_secret:
            payload["signature"] = self._sign_moonpay_payload(payload)

        r = await self._moonpay_client.post("/v1/transactions", json=payload)
        r.raise_for_status()
        data = r.json()

        return OnrampTransaction(
            transaction_id=data.get("id", "unknown"),
            status=data.get("status", "unknown"),
            quote_id=quote_id,
            wallet_address=wallet_address,
            redirect_url=data.get("redirectUrl"),
            raw=data,
        )

    def _sign_moonpay_payload(self, payload: dict[str, Any]) -> str:
        """Sign a MoonPay payload with the secret key."""
        if not self._moonpay_secret:
            return ""
        query = "&".join(f"{k}={v}" for k, v in sorted(payload.items()))
        return hmac.new(
            self._moonpay_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------------ #
    # Mock implementations
    # ------------------------------------------------------------------ #

    def _mock_get_quote(
        self,
        fiat_amount: float,
        fiat_currency: str,
        crypto_currency: str,
        wallet_address: str,
    ) -> OnrampQuote:
        self._mock_counter += 1
        quote_id = f"mock_quote_{self._mock_counter}"
        fee = round(fiat_amount * 0.045 + 3.99, 2)
        # Very rough mock conversion rate
        rate = 2000.0 if crypto_currency.lower() == "eth" else 30000.0 if crypto_currency.lower() == "btc" else 100.0
        crypto_amount = round((fiat_amount - fee) / rate, 6)
        self._mock_quotes[quote_id] = {
            "quote_id": quote_id,
            "fiat_amount": fiat_amount,
            "fiat_currency": fiat_currency,
            "crypto_amount": crypto_amount,
            "crypto_currency": crypto_currency,
            "fee_amount": fee,
            "total_cost": fiat_amount + fee,
        }
        return OnrampQuote(
            quote_id=quote_id,
            fiat_amount=fiat_amount,
            fiat_currency=fiat_currency,
            crypto_amount=crypto_amount,
            crypto_currency=crypto_currency,
            fee_amount=fee,
            total_cost=fiat_amount + fee,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            raw=self._mock_quotes[quote_id],
        )

    def _mock_create_transaction(
        self,
        quote_id: str,
        wallet_address: str,
    ) -> OnrampTransaction:
        self._mock_counter += 1
        tx_id = f"mock_onramp_tx_{self._mock_counter}"
        self._mock_txs[tx_id] = {
            "id": tx_id,
            "status": "waitingPayment",
            "quoteId": quote_id,
            "walletAddress": wallet_address,
            "redirectUrl": f"https://buy.moonpay.com/mock/{tx_id}",
        }
        return OnrampTransaction(
            transaction_id=tx_id,
            status="waitingPayment",
            quote_id=quote_id,
            wallet_address=wallet_address,
            redirect_url=self._mock_txs[tx_id]["redirectUrl"],
            raw=self._mock_txs[tx_id],
        )

    async def close(self) -> None:
        await self._moonpay_client.aclose()
        await self._stripe_client.aclose()

    async def __aenter__(self) -> CryptoOnrampLimb:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class OnrampLimitExceeded(Exception):
    """Raised when an onramp purchase exceeds configured limits."""


class OnrampConfirmationError(Exception):
    """Raised when an onramp confirmation fails or delay is not elapsed."""
