"""Tests for the payments modules."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from auton.limbs.payments.crypto_onramp import (
    CryptoOnrampLimb,
    OnrampConfirmationError,
    OnrampLimitExceeded,
    OnrampQuote,
    OnrampTransaction,
)
from auton.limbs.payments.stripe_client import (
    Invoice,
    PaymentIntent,
    StripePaymentsLimb,
)
from auton.security.spend_caps import SpendGuard


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def stripe_payments():
    # Skeleton mode
    return StripePaymentsLimb()


@pytest.fixture
def crypto_onramp():
    # Skeleton mode
    return CryptoOnrampLimb()


@pytest.fixture
def spend_guard(tmp_path):
    db = tmp_path / "spend.db"
    return SpendGuard(db_path=str(db))


# ------------------------------------------------------------------ #
# StripePaymentsLimb — skeleton mode
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_stripe_payments_health_check_skeleton(stripe_payments):
    health = await stripe_payments.health_check()
    assert health["status"] == "skeleton"
    assert health["mode"] == "mock"


@pytest.mark.asyncio
async def test_stripe_payments_create_payment_intent(stripe_payments):
    intent = await stripe_payments.create_payment_intent(
        amount=2000,
        currency="usd",
        customer_email="test@example.com",
    )
    assert isinstance(intent, PaymentIntent)
    assert intent.status == "requires_confirmation"
    assert intent.amount == 2000
    assert intent.currency == "usd"


@pytest.mark.asyncio
async def test_stripe_payments_capture_payment_intent(stripe_payments):
    intent = await stripe_payments.create_payment_intent(amount=5000)
    captured = await stripe_payments.capture_payment_intent(intent.intent_id)
    assert isinstance(captured, PaymentIntent)
    assert captured.status == "succeeded"


@pytest.mark.asyncio
async def test_stripe_payments_capture_unknown_intent(stripe_payments):
    with pytest.raises(ValueError):
        await stripe_payments.capture_payment_intent("unknown_id")


@pytest.mark.asyncio
async def test_stripe_payments_create_invoice(stripe_payments):
    invoice = await stripe_payments.create_invoice(
        customer_email="client@example.com",
        amount=10000,
        currency="usd",
        description="Consulting fee",
    )
    assert isinstance(invoice, Invoice)
    assert invoice.status == "draft"
    assert invoice.amount_due == 10000
    assert invoice.customer_email == "client@example.com"


@pytest.mark.asyncio
async def test_stripe_payments_finalize_invoice(stripe_payments):
    invoice = await stripe_payments.create_invoice(
        customer_email="client@example.com",
        amount=5000,
    )
    finalized = await stripe_payments.finalize_invoice(invoice.invoice_id)
    assert finalized.status == "open"


@pytest.mark.asyncio
async def test_stripe_payments_send_invoice(stripe_payments):
    invoice = await stripe_payments.create_invoice(
        customer_email="client@example.com",
        amount=5000,
    )
    sent = await stripe_payments.send_invoice(invoice.invoice_id)
    assert sent.status == "open"


@pytest.mark.asyncio
async def test_stripe_payments_get_invoice(stripe_payments):
    invoice = await stripe_payments.create_invoice(
        customer_email="client@example.com",
        amount=3000,
    )
    fetched = await stripe_payments.get_invoice(invoice.invoice_id)
    assert fetched.invoice_id == invoice.invoice_id


@pytest.mark.asyncio
async def test_stripe_payments_get_invoice_not_found(stripe_payments):
    with pytest.raises(ValueError):
        await stripe_payments.get_invoice("nonexistent")


@pytest.mark.asyncio
async def test_stripe_payments_webhook_payment_succeeded(stripe_payments):
    payload = {
        "type": "payment_intent.succeeded",
        "id": "evt_123",
        "data": {
            "object": {
                "id": "pi_123",
                "amount": 2000,
                "currency": "usd",
            },
        },
    }
    result = await stripe_payments.handle_webhook(payload)
    assert result["handled"] is True
    assert result["action"] == "record_revenue"
    assert result["amount"] == 2000


@pytest.mark.asyncio
async def test_stripe_payments_webhook_payment_failed(stripe_payments):
    payload = {
        "type": "payment_intent.payment_failed",
        "id": "evt_456",
        "data": {
            "object": {
                "id": "pi_456",
                "last_payment_error": {"message": "card declined"},
            },
        },
    }
    result = await stripe_payments.handle_webhook(payload)
    assert result["handled"] is True
    assert result["action"] == "log_failure"
    assert result["error"] == "card declined"


@pytest.mark.asyncio
async def test_stripe_payments_webhook_invoice_paid(stripe_payments):
    payload = {
        "type": "invoice.paid",
        "id": "evt_789",
        "data": {
            "object": {
                "id": "inv_789",
                "amount_paid": 15000,
            },
        },
    }
    result = await stripe_payments.handle_webhook(payload)
    assert result["handled"] is True
    assert result["action"] == "record_revenue"
    assert result["invoice_id"] == "inv_789"


@pytest.mark.asyncio
async def test_stripe_payments_execute_dispatch(stripe_payments):
    result = await stripe_payments.execute({
        "method": "create_payment_intent",
        "kwargs": {"amount": 1000},
    })
    assert isinstance(result, PaymentIntent)

    result = await stripe_payments.execute({
        "method": "create_invoice",
        "kwargs": {"customer_email": "a@b.com", "amount": 2000},
    })
    assert isinstance(result, Invoice)


@pytest.mark.asyncio
async def test_stripe_payments_get_cost_estimate(stripe_payments):
    fee = await stripe_payments.get_cost_estimate({
        "method": "create_payment_intent",
        "kwargs": {"amount": 10000},
    })
    # 2.9% + $0.30 of $100 = $2.90 + $0.30 = $3.20
    assert fee == pytest.approx(3.20, 0.01)

    fee = await stripe_payments.get_cost_estimate({
        "method": "create_invoice",
        "kwargs": {"amount": 5000},
    })
    # $50 * 0.029 + 0.30 = $1.75
    assert fee == pytest.approx(1.75, 0.01)


@pytest.mark.asyncio
async def test_stripe_payments_is_available():
    limb = StripePaymentsLimb()
    assert limb.is_available(0) is False
    assert limb.is_available(1) is True
    assert limb.is_available(2) is True


# ------------------------------------------------------------------ #
# CryptoOnrampLimb — skeleton mode
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_crypto_onramp_health_check_skeleton(crypto_onramp):
    health = await crypto_onramp.health_check()
    assert health["status"] == "skeleton"
    assert health["mode"] == "mock"


@pytest.mark.asyncio
async def test_crypto_onramp_get_quote_mock(crypto_onramp):
    quote = await crypto_onramp.get_quote(
        fiat_amount=100.0,
        fiat_currency="usd",
        crypto_currency="eth",
        wallet_address="0x123",
    )
    assert isinstance(quote, OnrampQuote)
    assert quote.fiat_amount == 100.0
    assert quote.fiat_currency == "usd"
    assert quote.crypto_currency == "eth"
    assert quote.fee_amount > 0


@pytest.mark.asyncio
async def test_crypto_onramp_create_transaction_mock(crypto_onramp):
    quote = await crypto_onramp.get_quote(
        fiat_amount=100.0,
        fiat_currency="usd",
        crypto_currency="eth",
        wallet_address="0x123",
    )
    tx = await crypto_onramp.create_transaction(
        quote_id=quote.quote_id,
        wallet_address="0x123",
    )
    assert isinstance(tx, OnrampTransaction)
    assert tx.status == "waitingPayment"
    assert tx.wallet_address == "0x123"


@pytest.mark.asyncio
async def test_crypto_onramp_large_purchase_requires_confirmation(crypto_onramp):
    quote = await crypto_onramp.get_quote(
        fiat_amount=600.0,
        fiat_currency="usd",
        crypto_currency="eth",
        wallet_address="0x123",
    )
    result = await crypto_onramp.create_transaction(
        quote_id=quote.quote_id,
        wallet_address="0x123",
    )
    assert isinstance(result, dict)
    assert result["status"] == "pending_confirmation"
    assert "confirmation_id" in result


@pytest.mark.asyncio
async def test_crypto_onramp_confirm_transaction_success(crypto_onramp):
    quote = await crypto_onramp.get_quote(
        fiat_amount=600.0,
        fiat_currency="usd",
        crypto_currency="eth",
        wallet_address="0x123",
    )
    result = await crypto_onramp.create_transaction(
        quote_id=quote.quote_id,
        wallet_address="0x123",
    )
    confirmation_id = result["confirmation_id"]

    # Force elapsed time
    pending = crypto_onramp._pending_confirmations[confirmation_id]
    pending["requested_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=crypto_onramp.CONFIRMATION_DELAY_SECONDS + 1)
    ).isoformat()

    tx = await crypto_onramp.confirm_transaction(confirmation_id)
    assert isinstance(tx, OnrampTransaction)
    assert tx.status == "waitingPayment"


@pytest.mark.asyncio
async def test_crypto_onramp_confirm_transaction_too_early(crypto_onramp):
    quote = await crypto_onramp.get_quote(
        fiat_amount=600.0,
        fiat_currency="usd",
        crypto_currency="eth",
        wallet_address="0x123",
    )
    result = await crypto_onramp.create_transaction(
        quote_id=quote.quote_id,
        wallet_address="0x123",
    )
    confirmation_id = result["confirmation_id"]

    with pytest.raises(OnrampConfirmationError):
        await crypto_onramp.confirm_transaction(confirmation_id)


@pytest.mark.asyncio
async def test_crypto_onramp_limit_exceeded(crypto_onramp, spend_guard):
    spend_guard.set_cap("crypto_onramp", daily=50.0)
    limb = CryptoOnrampLimb(spend_guard=spend_guard)
    quote = await limb.get_quote(
        fiat_amount=100.0,
        fiat_currency="usd",
        crypto_currency="eth",
        wallet_address="0x123",
    )
    with pytest.raises(OnrampLimitExceeded):
        await limb.create_transaction(quote_id=quote.quote_id, wallet_address="0x123")


@pytest.mark.asyncio
async def test_crypto_onramp_get_transaction_status(crypto_onramp):
    quote = await crypto_onramp.get_quote(
        fiat_amount=100.0,
        fiat_currency="usd",
        crypto_currency="eth",
        wallet_address="0x123",
    )
    tx = await crypto_onramp.create_transaction(
        quote_id=quote.quote_id,
        wallet_address="0x123",
    )
    status = await crypto_onramp.get_transaction_status(tx.transaction_id)
    assert status.get("id") == tx.transaction_id


@pytest.mark.asyncio
async def test_crypto_onramp_execute_dispatch(crypto_onramp):
    result = await crypto_onramp.execute({
        "method": "get_quote",
        "kwargs": {"fiat_amount": 200.0, "crypto_currency": "btc"},
    })
    assert isinstance(result, OnrampQuote)

    result = await crypto_onramp.execute({
        "method": "create_transaction",
        "kwargs": {"quote_id": result.quote_id, "wallet_address": "0xabc"},
    })
    assert isinstance(result, OnrampTransaction)


@pytest.mark.asyncio
async def test_crypto_onramp_get_cost_estimate(crypto_onramp):
    fee = await crypto_onramp.get_cost_estimate({
        "method": "get_quote",
        "kwargs": {"fiat_amount": 100.0},
    })
    # 4.5% + $3.99 of $100 = $4.50 + $3.99 = $8.49
    assert fee == pytest.approx(8.49, 0.01)


@pytest.mark.asyncio
async def test_crypto_onramp_is_available():
    limb = CryptoOnrampLimb()
    assert limb.is_available(0) is False
    assert limb.is_available(1) is False
    assert limb.is_available(2) is True
    assert limb.is_available(3) is True


# ------------------------------------------------------------------ #
# Environment variable safety
# ------------------------------------------------------------------ #


def test_stripe_reads_env_vars(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_123")
    limb = StripePaymentsLimb()
    assert limb._api_key == "sk_test_123"
    assert limb._webhook_secret == "whsec_123"


def test_stripe_no_env_vars_runs_skeleton():
    for key in ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"]:
        os.environ.pop(key, None)
    limb = StripePaymentsLimb()
    assert limb._api_key is None
    assert limb._webhook_secret is None


def test_moonpay_reads_env_vars(monkeypatch):
    monkeypatch.setenv("MOONPAY_API_KEY", "mpk_test_123")
    monkeypatch.setenv("MOONPAY_SECRET_KEY", "mps_test_123")
    limb = CryptoOnrampLimb()
    assert limb._moonpay_api_key == "mpk_test_123"
    assert limb._moonpay_secret == "mps_test_123"


def test_moonpay_no_env_vars_runs_skeleton():
    for key in ["MOONPAY_API_KEY", "MOONPAY_SECRET_KEY", "STRIPE_CRYPTO_SECRET_KEY"]:
        os.environ.pop(key, None)
    limb = CryptoOnrampLimb()
    assert limb._moonpay_api_key is None
    assert limb._moonpay_secret is None
    assert limb._stripe_crypto_key is None
