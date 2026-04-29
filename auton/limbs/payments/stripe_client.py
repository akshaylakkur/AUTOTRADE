"""Stripe payment processing and invoicing for Project ÆON.

Extends the basic StripeLimb with invoice generation, payment intent
management, and webhook handling for real-world payment flows.

Credentials:
  - STRIPE_SECRET_KEY
  - STRIPE_WEBHOOK_SECRET (optional, for webhook verification)
"""

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from auton.limbs.base_limb import BaseLimb
from auton.security.audit_trail import AuditLog
from auton.security.spend_caps import SpendGuard


@dataclass(frozen=True, slots=True)
class Invoice:
    invoice_id: str
    status: str
    amount_due: int  # cents
    currency: str
    customer_email: str | None
    hosted_invoice_url: str | None
    paid: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    intent_id: str
    status: str
    amount: int  # cents
    currency: str
    client_secret: str | None
    charges: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class StripePaymentsLimb(BaseLimb):
    """Async limb for Stripe payment processing and invoicing.

    Parameters
    ----------
    api_key:
        Stripe secret key.  When ``None`` reads from ``STRIPE_SECRET_KEY``.
    webhook_secret:
        Stripe webhook signing secret.  When ``None`` reads from
        ``STRIPE_WEBHOOK_SECRET``.
    base_url:
        Stripe API base URL.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        webhook_secret: str | None = None,
        base_url: str = "https://api.stripe.com",
        spend_guard: SpendGuard | None = None,
        audit_log: AuditLog | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key or os.environ.get("STRIPE_SECRET_KEY")
        self._webhook_secret = webhook_secret or os.environ.get("STRIPE_WEBHOOK_SECRET")
        self._base_url = base_url.rstrip("/")
        self._spend_guard = spend_guard
        self._audit_log = audit_log or AuditLog()

        auth = (self._api_key, "") if self._api_key else None
        self._client = httpx.AsyncClient(base_url=self._base_url, auth=auth, timeout=30.0)

        # Mock state for skeleton mode
        self._mock_invoices: dict[str, dict[str, Any]] = {}
        self._mock_intents: dict[str, dict[str, Any]] = {}
        self._mock_customers: dict[str, dict[str, Any]] = {}
        self._mock_counter = 0

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "create_payment_intent":
            return await self.create_payment_intent(**kwargs)
        if method == "capture_payment_intent":
            return await self.capture_payment_intent(**kwargs)
        if method == "create_invoice":
            return await self.create_invoice(**kwargs)
        if method == "finalize_invoice":
            return await self.finalize_invoice(**kwargs)
        if method == "send_invoice":
            return await self.send_invoice(**kwargs)
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        """Estimate Stripe processing fee (2.9% + $0.30 for US cards)."""
        method = action.get("method")
        if method in ("create_payment_intent", "create_invoice"):
            amount_cents = action.get("kwargs", {}).get("amount", 0)
            amount_usd = amount_cents / 100.0
            return round(amount_usd * 0.029 + 0.30, 2)
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 1

    async def health_check(self) -> dict[str, Any]:
        if self._api_key is None:
            return {"status": "skeleton", "mode": "mock"}
        try:
            start = asyncio.get_event_loop().time()
            r = await self._client.get("/v1/account")
            latency = (asyncio.get_event_loop().time() - start) * 1000
            return {
                "status": "healthy" if r.status_code == 200 else "degraded",
                "mode": "live",
                "latency_ms": round(latency, 2),
                "status_code": r.status_code,
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "unhealthy", "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Payment intents
    # ------------------------------------------------------------------ #

    async def create_payment_intent(
        self,
        amount: int,
        currency: str = "usd",
        customer_email: str | None = None,
        metadata: dict[str, str] | None = None,
        capture_method: str = "automatic",
    ) -> PaymentIntent:
        """Create a Stripe PaymentIntent.

        :param amount: Amount in the smallest currency unit (cents).
        :param currency: Three-letter ISO currency code.
        :param customer_email: Customer email for receipt.
        :param metadata: Key-value pairs for tracking.
        :param capture_method: ``automatic`` or ``manual``.
        """
        self._emit("limb.payment_intent.requested", {"amount": amount, "currency": currency})
        log_id = self._audit_log.pre_log("stripe.payment_intent.create", {
            "amount": amount,
            "currency": currency,
            "customer_email": customer_email,
        })

        try:
            if self._api_key:
                intent = await self._live_create_payment_intent(
                    amount, currency, customer_email, metadata, capture_method
                )
            else:
                intent = self._mock_create_payment_intent(
                    amount, currency, customer_email, metadata, capture_method
                )

            self._audit_log.post_log(log_id, {"intent_id": intent.intent_id, "status": intent.status})
            self._emit("limb.payment_intent.created", {"intent_id": intent.intent_id})
            return intent
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    async def capture_payment_intent(
        self,
        intent_id: str,
        amount: int | None = None,
    ) -> PaymentIntent:
        """Capture an authorized PaymentIntent (manual capture flow)."""
        self._emit("limb.payment_intent.capture_requested", {"intent_id": intent_id})
        log_id = self._audit_log.pre_log("stripe.payment_intent.capture", {"intent_id": intent_id, "amount": amount})

        try:
            if self._api_key:
                payload: dict[str, Any] = {}
                if amount is not None:
                    payload["amount_to_capture"] = amount
                r = await self._client.post(f"/v1/payment_intents/{intent_id}/capture", data=payload)
                r.raise_for_status()
                data = r.json()
                intent = _to_payment_intent(data)
            else:
                intent = self._mock_capture_payment_intent(intent_id, amount)

            self._audit_log.post_log(log_id, {"intent_id": intent.intent_id, "status": intent.status})
            self._emit("limb.payment_intent.captured", {"intent_id": intent.intent_id})
            return intent
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    # ------------------------------------------------------------------ #
    # Invoicing
    # ------------------------------------------------------------------ #

    async def create_invoice(
        self,
        customer_email: str,
        amount: int,
        currency: str = "usd",
        description: str = "",
        days_until_due: int = 30,
        metadata: dict[str, str] | None = None,
    ) -> Invoice:
        """Create a Stripe invoice for a customer.

        :param amount: Amount in cents.
        :param days_until_due: Days until the invoice is due.
        """
        self._emit("limb.invoice.requested", {"customer_email": customer_email, "amount": amount})
        log_id = self._audit_log.pre_log("stripe.invoice.create", {
            "customer_email": customer_email,
            "amount": amount,
            "currency": currency,
        })

        try:
            if self._api_key:
                # Create or retrieve customer
                customer_id = await self._get_or_create_customer(customer_email)

                # Create invoice item
                item_payload = {
                    "customer": customer_id,
                    "amount": amount,
                    "currency": currency,
                    "description": description,
                }
                await self._client.post("/v1/invoiceitems", data=item_payload)

                # Create draft invoice
                invoice_payload = {
                    "customer": customer_id,
                    "collection_method": "send_invoice",
                    "days_until_due": days_until_due,
                }
                if metadata:
                    for k, v in metadata.items():
                        invoice_payload[f"metadata[{k}]"] = v
                r = await self._client.post("/v1/invoices", data=invoice_payload)
                r.raise_for_status()
                data = r.json()
                invoice = _to_invoice(data)
            else:
                invoice = self._mock_create_invoice(customer_email, amount, currency, description)

            self._audit_log.post_log(log_id, {"invoice_id": invoice.invoice_id})
            self._emit("limb.invoice.created", {"invoice_id": invoice.invoice_id})
            return invoice
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    async def finalize_invoice(self, invoice_id: str) -> Invoice:
        """Finalize a draft invoice so it can be sent."""
        self._emit("limb.invoice.finalize_requested", {"invoice_id": invoice_id})
        log_id = self._audit_log.pre_log("stripe.invoice.finalize", {"invoice_id": invoice_id})

        try:
            if self._api_key:
                r = await self._client.post(f"/v1/invoices/{invoice_id}/finalize")
                r.raise_for_status()
                invoice = _to_invoice(r.json())
            else:
                invoice = self._mock_finalize_invoice(invoice_id)

            self._audit_log.post_log(log_id, {"invoice_id": invoice.invoice_id, "status": invoice.status})
            self._emit("limb.invoice.finalized", {"invoice_id": invoice.invoice_id})
            return invoice
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    async def send_invoice(self, invoice_id: str) -> Invoice:
        """Send a finalized invoice to the customer."""
        self._emit("limb.invoice.send_requested", {"invoice_id": invoice_id})
        log_id = self._audit_log.pre_log("stripe.invoice.send", {"invoice_id": invoice_id})

        try:
            if self._api_key:
                r = await self._client.post(f"/v1/invoices/{invoice_id}/send")
                r.raise_for_status()
                invoice = _to_invoice(r.json())
            else:
                invoice = self._mock_send_invoice(invoice_id)

            self._audit_log.post_log(log_id, {"invoice_id": invoice.invoice_id, "status": invoice.status})
            self._emit("limb.invoice.sent", {"invoice_id": invoice.invoice_id})
            return invoice
        except Exception as exc:
            self._audit_log.post_log(log_id, {"error": str(exc)})
            raise

    async def get_invoice(self, invoice_id: str) -> Invoice:
        if self._api_key:
            r = await self._client.get(f"/v1/invoices/{invoice_id}")
            if r.status_code == 404:
                raise ValueError(f"Invoice {invoice_id} not found")
            r.raise_for_status()
            return _to_invoice(r.json())
        data = self._mock_invoices.get(invoice_id)
        if data is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        return _to_invoice(data)

    # ------------------------------------------------------------------ #
    # Webhooks
    # ------------------------------------------------------------------ #

    async def handle_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a Stripe webhook event."""
        event_type = payload.get("type")
        event_id = payload.get("id")
        data = payload.get("data", {}).get("object", {})

        self._emit("limb.stripe.webhook.received", {"type": event_type, "id": event_id})
        log_id = self._audit_log.pre_log("stripe.webhook", {"type": event_type, "id": event_id})

        result = {"event_type": event_type, "handled": True}

        if event_type == "payment_intent.succeeded":
            result["action"] = "record_revenue"
            result["amount"] = data.get("amount", 0)
            result["currency"] = data.get("currency", "usd")
        elif event_type == "payment_intent.payment_failed":
            result["action"] = "log_failure"
            result["error"] = data.get("last_payment_error", {}).get("message", "unknown")
        elif event_type == "invoice.paid":
            result["action"] = "record_revenue"
            result["invoice_id"] = data.get("id")
            result["amount"] = data.get("amount_paid", 0)
        elif event_type == "invoice.payment_failed":
            result["action"] = "log_failure"
            result["invoice_id"] = data.get("id")
            result["attempt_count"] = data.get("attempt_count", 0)

        self._audit_log.post_log(log_id, result)
        self._emit("limb.stripe.webhook.processed", result)
        return result

    # ------------------------------------------------------------------ #
    # Live helpers
    # ------------------------------------------------------------------ #

    async def _live_create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None,
        metadata: dict[str, str] | None,
        capture_method: str,
    ) -> PaymentIntent:
        payload: dict[str, Any] = {
            "amount": amount,
            "currency": currency,
            "capture_method": capture_method,
        }
        if customer_email:
            payload["receipt_email"] = customer_email
        if metadata:
            for k, v in metadata.items():
                payload[f"metadata[{k}]"] = v
        r = await self._client.post("/v1/payment_intents", data=payload)
        r.raise_for_status()
        return _to_payment_intent(r.json())

    async def _get_or_create_customer(self, email: str) -> str:
        r = await self._client.get("/v1/customers", params={"email": email, "limit": 1})
        r.raise_for_status()
        data = r.json()
        if data.get("data"):
            return data["data"][0]["id"]

        r = await self._client.post("/v1/customers", data={"email": email})
        r.raise_for_status()
        return r.json()["id"]

    # ------------------------------------------------------------------ #
    # Mock implementations
    # ------------------------------------------------------------------ #

    def _mock_create_payment_intent(
        self,
        amount: int,
        currency: str,
        customer_email: str | None,
        metadata: dict[str, str] | None,
        capture_method: str,
    ) -> PaymentIntent:
        self._mock_counter += 1
        intent_id = f"mock_pi_{self._mock_counter}"
        self._mock_intents[intent_id] = {
            "id": intent_id,
            "status": "requires_confirmation",
            "amount": amount,
            "currency": currency,
            "client_secret": f"{intent_id}_secret",
            "charges": {"data": []},
        }
        return _to_payment_intent(self._mock_intents[intent_id])

    def _mock_capture_payment_intent(
        self,
        intent_id: str,
        amount: int | None,
    ) -> PaymentIntent:
        data = self._mock_intents.get(intent_id)
        if not data:
            raise ValueError(f"PaymentIntent {intent_id} not found")
        data["status"] = "succeeded"
        if amount is not None:
            data["amount_received"] = amount
        return _to_payment_intent(data)

    def _mock_create_invoice(
        self,
        customer_email: str,
        amount: int,
        currency: str,
        description: str,
    ) -> Invoice:
        self._mock_counter += 1
        invoice_id = f"mock_inv_{self._mock_counter}"
        self._mock_invoices[invoice_id] = {
            "id": invoice_id,
            "status": "draft",
            "amount_due": amount,
            "currency": currency,
            "customer_email": customer_email,
            "hosted_invoice_url": f"https://invoice.stripe.com/mock/{invoice_id}",
            "paid": False,
        }
        return _to_invoice(self._mock_invoices[invoice_id])

    def _mock_finalize_invoice(self, invoice_id: str) -> Invoice:
        data = self._mock_invoices.get(invoice_id)
        if not data:
            raise ValueError(f"Invoice {invoice_id} not found")
        data["status"] = "open"
        return _to_invoice(data)

    def _mock_send_invoice(self, invoice_id: str) -> Invoice:
        data = self._mock_invoices.get(invoice_id)
        if not data:
            raise ValueError(f"Invoice {invoice_id} not found")
        data["status"] = "open"
        return _to_invoice(data)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> StripePaymentsLimb:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _to_payment_intent(data: dict[str, Any]) -> PaymentIntent:
    return PaymentIntent(
        intent_id=data["id"],
        status=data.get("status", "unknown"),
        amount=data.get("amount", 0),
        currency=data.get("currency", "usd"),
        client_secret=data.get("client_secret"),
        charges=data.get("charges", {}).get("data", []),
        raw=data,
    )


def _to_invoice(data: dict[str, Any]) -> Invoice:
    return Invoice(
        invoice_id=data["id"],
        status=data.get("status", "unknown"),
        amount_due=data.get("amount_due", 0),
        currency=data.get("currency", "usd"),
        customer_email=data.get("customer_email") or data.get("customer", {}).get("email"),
        hosted_invoice_url=data.get("hosted_invoice_url"),
        paid=data.get("paid", False),
        raw=data,
    )
