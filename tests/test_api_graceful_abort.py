"""Tests for graceful API abort and free API fallback behavior."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from auton.core.config import AeonConfig, CapabilityRegistry
from auton.limbs.communications.email_client import EmailClient, SMTPConfig
from auton.limbs.payments.stripe_client import StripePaymentsLimb
from auton.limbs.trading.binance_spot_trading import BinanceSpotTradingLimb
from auton.senses.intelligence.search_engine import SearchEngine
from auton.senses.market_data.coingecko_connector import CoinGeckoConnector
from auton.senses.market_data.yahoo_finance_connector import YahooFinanceConnector


# ------------------------------------------------------------------
# CapabilityRegistry tests
# ------------------------------------------------------------------
class TestCapabilityRegistry:
    def test_binance_trading_available_when_keys_present(self, monkeypatch):
        monkeypatch.setenv("BINANCE_API_KEY", "key")
        monkeypatch.setenv("BINANCE_SECRET_KEY", "secret")
        assert CapabilityRegistry.is_available("binance_trading") is True

    def test_binance_trading_unavailable_when_keys_missing(self, monkeypatch):
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_SECRET_KEY", raising=False)
        assert CapabilityRegistry.is_available("binance_trading") is False

    def test_stripe_payments_available_when_key_present(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
        assert CapabilityRegistry.is_available("stripe_payments") is True

    def test_stripe_payments_unavailable_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        assert CapabilityRegistry.is_available("stripe_payments") is False

    def test_email_available_when_smtp_configured(self, monkeypatch):
        monkeypatch.setenv("AEON_APPROVAL_EMAIL_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("AEON_APPROVAL_EMAIL_SENDER", "a@example.com")
        monkeypatch.setenv("AEON_APPROVAL_EMAIL_PASSWORD", "pass")
        assert CapabilityRegistry.is_available("email") is True

    def test_email_unavailable_when_smtp_missing(self, monkeypatch):
        monkeypatch.delenv("AEON_APPROVAL_EMAIL_SMTP_HOST", raising=False)
        monkeypatch.delenv("AEON_APPROVAL_EMAIL_SENDER", raising=False)
        monkeypatch.delenv("AEON_APPROVAL_EMAIL_PASSWORD", raising=False)
        assert CapabilityRegistry.is_available("email") is False

    def test_serpapi_search_available_when_key_present(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "secret")
        assert CapabilityRegistry.is_available("serpapi_search") is True

    def test_serpapi_search_unavailable_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        assert CapabilityRegistry.is_available("serpapi_search") is False

    def test_missing_vars_returns_empty_when_configured(self, monkeypatch):
        monkeypatch.setenv("BINANCE_API_KEY", "k")
        monkeypatch.setenv("BINANCE_SECRET_KEY", "s")
        assert CapabilityRegistry.missing_vars("binance_trading") == []

    def test_missing_vars_returns_missing_keys(self, monkeypatch):
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.setenv("BINANCE_SECRET_KEY", "s")
        missing = CapabilityRegistry.missing_vars("binance_trading")
        assert "BINANCE_API_KEY" in missing

    def test_check_all_returns_dict(self):
        result = CapabilityRegistry.check_all()
        assert isinstance(result, dict)
        assert "binance_trading" in result


# ------------------------------------------------------------------
# is_configured classmethod tests
# ------------------------------------------------------------------
class TestIsConfigured:
    def test_binance_spot_trading_configured(self, monkeypatch):
        monkeypatch.setenv("BINANCE_API_KEY", "key")
        monkeypatch.setenv("BINANCE_SECRET_KEY", "secret")
        assert BinanceSpotTradingLimb.is_configured() is True

    def test_binance_spot_trading_not_configured(self, monkeypatch):
        monkeypatch.delenv("BINANCE_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_SECRET_KEY", raising=False)
        assert BinanceSpotTradingLimb.is_configured() is False

    def test_stripe_payments_configured(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
        assert StripePaymentsLimb.is_configured() is True

    def test_stripe_payments_not_configured(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        assert StripePaymentsLimb.is_configured() is False

    def test_email_client_configured_with_config(self):
        cfg = SMTPConfig(
            host="smtp.example.com",
            port=587,
            username="a@example.com",
            password="pass",
        )
        assert EmailClient.is_configured(cfg) is True

    def test_email_client_not_configured_with_empty_config(self):
        cfg = SMTPConfig(
            host="",
            port=587,
            username="",
            password="",
        )
        assert EmailClient.is_configured(cfg) is False

    def test_search_engine_serpapi_configured(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "secret")
        assert SearchEngine.is_configured("serpapi") is True

    def test_search_engine_serpapi_not_configured(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        assert SearchEngine.is_configured("serpapi") is False

    def test_search_engine_brave_configured(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "secret")
        assert SearchEngine.is_configured("brave") is True

    def test_search_engine_brave_not_configured(self, monkeypatch):
        monkeypatch.delenv("BRAVE_API_KEY", raising=False)
        assert SearchEngine.is_configured("brave") is False


# ------------------------------------------------------------------
# AEON orchestrator graceful abort tests
# ------------------------------------------------------------------
@pytest.fixture
def no_api_keys(monkeypatch):
    """Ensure all external API keys are absent."""
    for key in [
        "BINANCE_API_KEY",
        "BINANCE_SECRET_KEY",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "AEON_APPROVAL_EMAIL_SMTP_HOST",
        "AEON_APPROVAL_EMAIL_SENDER",
        "AEON_APPROVAL_EMAIL_PASSWORD",
        "AEON_APPROVAL_EMAIL_RECIPIENT",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def aeon_no_keys(no_api_keys, monkeypatch):
    """Return an AEON instance with no external API keys."""
    monkeypatch.setenv("AEON_VAULT_KEY", Fernet.generate_key().decode())
    with patch("auton.aeon.AdaptionEngine") as MockAdaption:
        MockAdaption.return_value = MagicMock()
        from auton.aeon import AEON

        return AEON()


@pytest.mark.asyncio
async def test_trading_limb_none_when_keys_missing(aeon_no_keys):
    assert aeon_no_keys._trading_limb is None


@pytest.mark.asyncio
async def test_payments_limb_none_when_keys_missing(aeon_no_keys):
    assert aeon_no_keys._payments_limb is None


@pytest.mark.asyncio
async def test_email_client_none_when_smtp_missing(aeon_no_keys):
    assert aeon_no_keys._email_client is None


@pytest.mark.asyncio
async def test_trading_gateway_none_when_keys_missing(aeon_no_keys):
    assert aeon_no_keys._trading_gateway is None


@pytest.mark.asyncio
async def test_payments_gateway_none_when_keys_missing(aeon_no_keys):
    assert aeon_no_keys._payments_gateway is None


@pytest.mark.asyncio
async def test_fallback_connectors_present(aeon_no_keys):
    assert len(aeon_no_keys._fallback_connectors) == 2
    assert isinstance(aeon_no_keys._fallback_connectors[0], CoinGeckoConnector)
    assert isinstance(aeon_no_keys._fallback_connectors[1], YahooFinanceConnector)


@pytest.mark.asyncio
async def test_shutdown_with_none_limbs_no_crash(aeon_no_keys):
    """Shutdown should not crash when limbs are None."""
    aeon_no_keys._running = True
    with patch.object(aeon_no_keys._env_sensor, "stop", new_callable=AsyncMock):
        with patch.object(aeon_no_keys._market_connector, "disconnect", new_callable=AsyncMock):
            with patch.object(aeon_no_keys._opportunity_monitor, "stop", new_callable=AsyncMock):
                with patch.object(aeon_no_keys._llm, "close", new_callable=AsyncMock):
                    with patch.object(aeon_no_keys._event_bus, "stop", new_callable=AsyncMock):
                        with patch.object(aeon_no_keys._state_machine, "transition_to", new_callable=AsyncMock):
                            with patch.object(aeon_no_keys._consciousness, "remember"):
                                with patch.object(aeon_no_keys._consciousness, "close"):
                                    # Should not raise
                                    await aeon_no_keys.shutdown()


@pytest.mark.asyncio
async def test_decision_made_skips_trade_when_limb_none(aeon_no_keys):
    """DecisionMade for trading should be skipped gracefully when limb is None."""
    from auton.core.events import DecisionMade

    event = DecisionMade(
        action="buy BTC",
        expected_roi=0.05,
        confidence=0.8,
        risk_score=0.2,
        required_budget=5.0,
        strategy="trading",
        metadata={
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": 0.01,
            "decision_id": "test-decision-001",
        },
    )
    with patch.object(aeon_no_keys._consciousness, "resolve_decision") as mock_resolve:
        await aeon_no_keys._on_decision_made(event)
        mock_resolve.assert_called_once()
        assert "unavailable" in mock_resolve.call_args[1].get("notes", "")


# ------------------------------------------------------------------
# Free connector tests
# ------------------------------------------------------------------
class TestCoinGeckoConnector:
    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        conn = CoinGeckoConnector()
        assert not conn.connected
        await conn.connect()
        assert conn.connected
        await conn.disconnect()
        assert not conn.connected

    @pytest.mark.asyncio
    async def test_get_ticker(self):
        conn = CoinGeckoConnector()
        await conn.connect()
        try:
            with patch.object(
                conn._client, "get", return_value=MagicMock(
                    raise_for_status=MagicMock(),
                    json=MagicMock(return_value={
                        "bitcoin": {"usd": 50000.0, "usd_24h_vol": 1000000000.0, "usd_24h_change": 2.5}
                    }),
                )
            ):
                md = await conn.get_ticker("BTCUSDT")
                assert md.source == "coingecko"
                assert md.symbol == "BTCUSDT"
                assert "price" in md.data
        finally:
            await conn.disconnect()

    @pytest.mark.asyncio
    async def test_get_orderbook_returns_empty(self):
        conn = CoinGeckoConnector()
        await conn.connect()
        try:
            ob = await conn.get_orderbook("BTCUSDT")
            assert ob.bids == []
            assert ob.asks == []
        finally:
            await conn.disconnect()

    def test_subscription_cost_is_free(self):
        conn = CoinGeckoConnector()
        assert conn.get_subscription_cost() == {"monthly": 0.0, "daily": 0.0}

    def test_is_available_tier_0(self):
        conn = CoinGeckoConnector()
        assert conn.is_available(0) is True


class TestYahooFinanceConnector:
    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        conn = YahooFinanceConnector()
        assert not conn.connected
        await conn.connect()
        assert conn.connected
        await conn.disconnect()
        assert not conn.connected

    @pytest.mark.asyncio
    async def test_get_ticker(self):
        conn = YahooFinanceConnector()
        await conn.connect()
        try:
            with patch.object(
                conn._client, "get", return_value=MagicMock(
                    raise_for_status=MagicMock(),
                    json=MagicMock(return_value={
                        "chart": {
                            "result": [{
                                "meta": {
                                    "regularMarketPrice": 50000.0,
                                    "previousClose": 49000.0,
                                    "regularMarketVolume": 1000000.0,
                                }
                            }]
                        }
                    }),
                )
            ):
                md = await conn.get_ticker("BTCUSDT")
                assert md.source == "yahoo_finance"
                assert md.symbol == "BTCUSDT"
                assert "price" in md.data
        finally:
            await conn.disconnect()

    @pytest.mark.asyncio
    async def test_get_orderbook_returns_empty(self):
        conn = YahooFinanceConnector()
        await conn.connect()
        try:
            ob = await conn.get_orderbook("BTCUSDT")
            assert ob.bids == []
            assert ob.asks == []
        finally:
            await conn.disconnect()

    def test_subscription_cost_is_free(self):
        conn = YahooFinanceConnector()
        assert conn.get_subscription_cost() == {"monthly": 0.0, "daily": 0.0}

    def test_is_available_tier_0(self):
        conn = YahooFinanceConnector()
        assert conn.is_available(0) is True
