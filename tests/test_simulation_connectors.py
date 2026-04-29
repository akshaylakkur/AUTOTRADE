"""Tests for simulation data connectors."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from auton.limbs.commerce.stripe_limb import StripeLimb
from auton.limbs.dataclasses import OrderResult, OrderSide, OrderStatus, Product
from auton.limbs.trading.binance_spot_trading import BinanceSpotTradingLimb
from auton.senses.dataclasses import Candle, MarketData, OrderBook
from auton.simulation.clock import SimulationClock
from auton.simulation.connectors import (
    CommerceSimulator,
    CostEstimator,
    MarketDataSimulator,
    SimulatedData,
    WebResearchSimulator,
)
from auton.simulation.recorder import SimulationRecorder
from auton.simulation.wallet import SimulatedWallet


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_connector() -> AsyncMock:
    conn = AsyncMock()
    conn.connected = False
    conn.get_ticker = AsyncMock(
        return_value=MarketData(
            source="mock",
            symbol="BTCUSDT",
            data={"price": "21000.0"},
        )
    )
    conn.get_klines = AsyncMock(
        return_value=[
            Candle(
                source="mock",
                symbol="BTCUSDT",
                interval="1h",
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=100.0,
            )
        ]
    )
    conn.get_orderbook = AsyncMock(
        return_value=OrderBook(
            source="mock",
            symbol="BTCUSDT",
            bids=[(1.0, 2.0)],
            asks=[(1.5, 3.0)],
        )
    )
    conn.get_subscription_cost = MagicMock(return_value={"monthly": 0.0, "daily": 0.0})
    return conn


@pytest.fixture
def mock_trading_limb() -> AsyncMock:
    limb = AsyncMock(spec=BinanceSpotTradingLimb)
    limb.paper = True
    limb.place_order = AsyncMock(
        return_value=OrderResult(
            order_id="PAPER-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            status=OrderStatus.FILLED,
            executed_qty=0.1,
            cummulative_quote_qty=100.0,
            price=1000.0,
            order_type="MARKET",
        )
    )
    limb.cancel_order = AsyncMock(return_value={"status": "CANCELED"})
    limb.get_open_orders = AsyncMock(return_value=[])
    limb.get_account_balance = AsyncMock(
        return_value={
            "balances": [
                {"asset": "USDT", "free": 9000.0, "locked": 0.0},
                {"asset": "BTC", "free": 0.1, "locked": 0.0},
            ]
        }
    )
    limb._estimate_fee = MagicMock(return_value=0.1)
    return limb


@pytest.fixture
def wallet() -> SimulatedWallet:
    return SimulatedWallet(initial_balance=100.0)


@pytest.fixture
def recorder() -> SimulationRecorder:
    return SimulationRecorder()


@pytest.fixture
def clock() -> SimulationClock:
    return SimulationClock(start=datetime(2024, 1, 1, tzinfo=timezone.utc))


# =============================================================================
# MarketDataSimulator
# =============================================================================


class TestMarketDataSimulator:
    @pytest.mark.asyncio
    async def test_get_ticker(self, mock_connector: AsyncMock, recorder: SimulationRecorder, clock: SimulationClock) -> None:
        sim = MarketDataSimulator(
            connectors={"mock": mock_connector},
            recorder=recorder,
            clock=clock,
        )
        result = await sim.get_ticker("mock", "BTCUSDT")
        assert isinstance(result, SimulatedData)
        assert result.source == "mock"
        assert result.data_type == "ticker"
        assert result.simulated is True
        assert result.sim_time == clock.now()
        mock_connector.connect.assert_awaited_once()
        mock_connector.get_ticker.assert_awaited_once_with("BTCUSDT")
        assert recorder.get_event_count(category="market_data", action="ticker_fetched") == 1

    @pytest.mark.asyncio
    async def test_get_ticker_unknown_connector(self) -> None:
        sim = MarketDataSimulator()
        with pytest.raises(ValueError, match="Unknown connector"):
            await sim.get_ticker("missing", "BTCUSDT")

    @pytest.mark.asyncio
    async def test_get_klines(self, mock_connector: AsyncMock, recorder: SimulationRecorder, clock: SimulationClock) -> None:
        sim = MarketDataSimulator(
            connectors={"mock": mock_connector},
            recorder=recorder,
            clock=clock,
        )
        result = await sim.get_klines("mock", "BTCUSDT", "1h", limit=10)
        assert isinstance(result, SimulatedData)
        assert result.data_type == "klines"
        assert result.metadata["count"] == 1
        mock_connector.get_klines.assert_awaited_once_with("BTCUSDT", "1h", 10)

    @pytest.mark.asyncio
    async def test_get_orderbook(self, mock_connector: AsyncMock, recorder: SimulationRecorder, clock: SimulationClock) -> None:
        sim = MarketDataSimulator(
            connectors={"mock": mock_connector},
            recorder=recorder,
            clock=clock,
        )
        result = await sim.get_orderbook("mock", "BTCUSDT", limit=50)
        assert isinstance(result, SimulatedData)
        assert result.data_type == "orderbook"
        mock_connector.get_orderbook.assert_awaited_once_with("BTCUSDT", 50)

    @pytest.mark.asyncio
    async def test_get_orderbook_unsupported(self, mock_connector: AsyncMock) -> None:
        del mock_connector.get_orderbook
        sim = MarketDataSimulator(connectors={"mock": mock_connector})
        with pytest.raises(RuntimeError, match="does not support orderbook"):
            await sim.get_orderbook("mock", "BTCUSDT")

    @pytest.mark.asyncio
    async def test_place_order_debits_wallet(
        self, mock_trading_limb: AsyncMock, wallet: SimulatedWallet, recorder: SimulationRecorder, clock: SimulationClock
    ) -> None:
        sim = MarketDataSimulator(
            trading_limb=mock_trading_limb,
            wallet=wallet,
            recorder=recorder,
            clock=clock,
        )
        result = await sim.place_order("BTCUSDT", "BUY", 0.1, "MARKET")
        assert isinstance(result, OrderResult)
        assert result.order_id == "PAPER-1"
        assert wallet.get_balance() == pytest.approx(99.9)  # 100 - 0.1 fee
        assert recorder.get_event_count(category="trade", action="order_placed") == 1

    @pytest.mark.asyncio
    async def test_place_order_no_wallet(self, mock_trading_limb: AsyncMock) -> None:
        sim = MarketDataSimulator(trading_limb=mock_trading_limb)
        result = await sim.place_order("BTCUSDT", "BUY", 0.1, "MARKET")
        assert result.order_id == "PAPER-1"

    @pytest.mark.asyncio
    async def test_cancel_order(self, mock_trading_limb: AsyncMock, recorder: SimulationRecorder) -> None:
        sim = MarketDataSimulator(trading_limb=mock_trading_limb, recorder=recorder)
        result = await sim.cancel_order("BTCUSDT", "PAPER-1")
        assert result["status"] == "CANCELED"
        mock_trading_limb.cancel_order.assert_awaited_once_with("BTCUSDT", "PAPER-1")

    @pytest.mark.asyncio
    async def test_get_open_orders(self, mock_trading_limb: AsyncMock) -> None:
        sim = MarketDataSimulator(trading_limb=mock_trading_limb)
        orders = await sim.get_open_orders("BTCUSDT")
        assert orders == []
        mock_trading_limb.get_open_orders.assert_awaited_once_with("BTCUSDT")

    @pytest.mark.asyncio
    async def test_get_account_balance(self, mock_trading_limb: AsyncMock) -> None:
        sim = MarketDataSimulator(trading_limb=mock_trading_limb)
        bal = await sim.get_account_balance()
        assert "balances" in bal

    @pytest.mark.asyncio
    async def test_get_portfolio_value(self, mock_trading_limb: AsyncMock) -> None:
        sim = MarketDataSimulator(trading_limb=mock_trading_limb)
        val = await sim.get_portfolio_value()
        assert val == pytest.approx(9000.1)

    @pytest.mark.asyncio
    async def test_close_disconnects_connectors(self, mock_connector: AsyncMock, mock_trading_limb: AsyncMock) -> None:
        mock_connector.connected = True
        sim = MarketDataSimulator(
            connectors={"mock": mock_connector},
            trading_limb=mock_trading_limb,
        )
        await sim.close()
        mock_connector.disconnect.assert_awaited_once()
        mock_trading_limb.close.assert_awaited_once()

    def test_sim_time_with_clock(self, clock: SimulationClock) -> None:
        sim = MarketDataSimulator(clock=clock)
        assert sim._sim_time() == clock.now()

    def test_sim_time_without_clock(self) -> None:
        sim = MarketDataSimulator()
        before = datetime.now(timezone.utc)
        t = sim._sim_time()
        after = datetime.now(timezone.utc)
        assert before <= t <= after


# =============================================================================
# WebResearchSimulator
# =============================================================================


class TestWebResearchSimulator:
    @pytest.mark.asyncio
    async def test_search_success(self, recorder: SimulationRecorder, clock: SimulationClock) -> None:
        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={"title": "Bitcoin", "extract": "A cryptocurrency."})
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        sim = WebResearchSimulator(
            recorder=recorder,
            clock=clock,
            http_client=mock_client,
        )
        result = await sim.search("bitcoin", max_results=3)
        assert isinstance(result, SimulatedData)
        assert result.source == "web_research"
        assert result.data_type == "search_result"
        assert result.simulated is True
        assert result.payload["title"] == "Bitcoin"
        assert result.metadata["query"] == "bitcoin"
        assert recorder.get_event_count(category="research", action="web_search") == 1

    @pytest.mark.asyncio
    async def test_search_error(self, recorder: SimulationRecorder) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.HTTPError("Network error"))

        sim = WebResearchSimulator(http_client=mock_client, recorder=recorder)
        result = await sim.search("fail_query")
        assert isinstance(result, SimulatedData)
        assert "error" in result.payload
        assert result.payload["query"] == "fail_query"

    @pytest.mark.asyncio
    async def test_multi_search(self, recorder: SimulationRecorder) -> None:
        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={"title": "Test"})
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        sim = WebResearchSimulator(http_client=mock_client, recorder=recorder)
        results = await sim.multi_search(["a", "b", "c"])
        assert len(results) == 3
        assert all(isinstance(r, SimulatedData) for r in results)
        assert mock_client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        sim = WebResearchSimulator(http_client=mock_client)
        await sim.close()
        mock_client.aclose.assert_awaited_once()


# =============================================================================
# CommerceSimulator
# =============================================================================


class TestCommerceSimulator:
    @pytest.mark.asyncio
    async def test_create_product_debits_wallet(self, wallet: SimulatedWallet, recorder: SimulationRecorder, clock: SimulationClock) -> None:
        sim = CommerceSimulator(wallet=wallet, recorder=recorder, clock=clock)
        result = await sim.create_product(name="Test Widget", price_cents=499)
        assert isinstance(result, SimulatedData)
        assert result.data_type == "product"
        assert result.simulated is True
        assert isinstance(result.payload, Product)
        assert result.payload.name == "Test Widget"
        assert wallet.get_balance() == pytest.approx(99.70)  # 100 - 0.30 fee
        assert recorder.get_event_count(category="commerce", action="product_created") == 1

    @pytest.mark.asyncio
    async def test_create_checkout_session(self, wallet: SimulatedWallet, recorder: SimulationRecorder) -> None:
        sim = CommerceSimulator(wallet=wallet, recorder=recorder)
        product = await sim.create_product(name="Widget", price_cents=100)
        result = await sim.create_checkout_session(
            product_id=product.payload.product_id,
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
        )
        assert isinstance(result, SimulatedData)
        assert result.data_type == "checkout_session"
        assert result.simulated is True
        assert recorder.get_event_count(category="commerce", action="checkout_created") == 1

    @pytest.mark.asyncio
    async def test_simulate_purchase(self, wallet: SimulatedWallet, recorder: SimulationRecorder) -> None:
        sim = CommerceSimulator(wallet=wallet, recorder=recorder)
        result = await sim.simulate_purchase(session_id="sess_1", product_price_cents=1000)
        assert isinstance(result, SimulatedData)
        assert result.data_type == "purchase"
        assert result.payload["revenue"] == 10.0
        assert result.payload["fee"] == pytest.approx(0.59)  # 10 * 0.029 + 0.30
        assert result.payload["net"] == pytest.approx(9.41)
        assert wallet.get_balance() == pytest.approx(109.41)  # 100 + 9.41
        assert recorder.get_event_count(category="commerce", action="purchase_simulated") == 1

    @pytest.mark.asyncio
    async def test_simulate_purchase_zero_revenue(self, wallet: SimulatedWallet) -> None:
        sim = CommerceSimulator(wallet=wallet)
        result = await sim.simulate_purchase(session_id="sess_2", product_price_cents=0)
        assert result.payload["net"] == 0.0
        assert wallet.get_balance() == 100.0

    @pytest.mark.asyncio
    async def test_list_products(self, recorder: SimulationRecorder) -> None:
        sim = CommerceSimulator(recorder=recorder)
        await sim.create_product(name="A", price_cents=100)
        await sim.create_product(name="B", price_cents=200)
        result = await sim.list_products()
        assert isinstance(result, SimulatedData)
        assert result.data_type == "product_list"
        assert len(result.payload) == 2
        assert result.metadata["count"] == 2
        assert recorder.get_event_count(category="commerce", action="products_listed") == 1

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        stripe_limb = AsyncMock(spec=StripeLimb)
        sim = CommerceSimulator(stripe_limb=stripe_limb)
        await sim.close()
        stripe_limb.close.assert_awaited_once()


# =============================================================================
# CostEstimator
# =============================================================================


class TestCostEstimator:
    def test_estimate_trading_cost(self, recorder: SimulationRecorder, clock: SimulationClock) -> None:
        est = CostEstimator(recorder=recorder, clock=clock)
        result = est.estimate_trading_cost("BTCUSDT", 1.0, 1000.0, exchange="binance", side="taker")
        assert isinstance(result, SimulatedData)
        assert result.data_type == "trading_cost_estimate"
        assert result.payload["estimated_cost"] == pytest.approx(1.0)  # 1 * 1000 * 0.001
        assert result.payload["exchange"] == "binance"
        assert recorder.get_event_count(category="cost", action="trading_estimate") == 1

    def test_estimate_trading_cost_unknown_exchange(self) -> None:
        est = CostEstimator()
        result = est.estimate_trading_cost("BTCUSDT", 1.0, 1000.0, exchange="unknown")
        assert result.payload["fee_rate"] == pytest.approx(0.001)

    def test_estimate_trading_cost_maker(self) -> None:
        est = CostEstimator()
        result = est.estimate_trading_cost("BTCUSDT", 1.0, 1000.0, exchange="coinbase", side="maker")
        assert result.payload["fee_rate"] == pytest.approx(0.004)

    def test_estimate_llm_cost(self, recorder: SimulationRecorder) -> None:
        est = CostEstimator(recorder=recorder)
        result = est.estimate_llm_cost("gpt-4", tokens_in=2000, tokens_out=1000)
        assert isinstance(result, SimulatedData)
        assert result.data_type == "llm_cost_estimate"
        # (2000/1000)*0.03 + (1000/1000)*0.06 = 0.06 + 0.06 = 0.12
        assert result.payload["estimated_cost"] == pytest.approx(0.12)
        assert recorder.get_event_count(category="cost", action="llm_estimate") == 1

    def test_estimate_llm_cost_unknown_model(self) -> None:
        est = CostEstimator()
        result = est.estimate_llm_cost("unknown-model", tokens_in=1000, tokens_out=500)
        assert result.payload["estimated_cost"] == pytest.approx(0.06)  # defaults to gpt-4 rates

    def test_estimate_compute_cost(self, recorder: SimulationRecorder) -> None:
        est = CostEstimator(recorder=recorder)
        result = est.estimate_compute_cost("cpu-medium", hours=10.0)
        assert isinstance(result, SimulatedData)
        assert result.data_type == "compute_cost_estimate"
        assert result.payload["estimated_cost"] == pytest.approx(2.0)
        assert recorder.get_event_count(category="cost", action="compute_estimate") == 1

    def test_estimate_compute_cost_unknown_instance(self) -> None:
        est = CostEstimator()
        result = est.estimate_compute_cost("unknown", hours=1.0)
        assert result.payload["estimated_cost"] == pytest.approx(0.05)

    def test_estimate_data_cost(self, recorder: SimulationRecorder) -> None:
        mock_conn = MagicMock()
        mock_conn.__class__.__name__ = "MockConnector"
        mock_conn.get_subscription_cost = MagicMock(return_value={"monthly": 99.0, "daily": 3.3})
        est = CostEstimator(recorder=recorder)
        result = est.estimate_data_cost(mock_conn)
        assert isinstance(result, SimulatedData)
        assert result.data_type == "data_cost_estimate"
        assert result.payload["cost"]["daily"] == pytest.approx(3.3)
        assert result.payload["connector"] == "MockConnector"
        assert recorder.get_event_count(category="cost", action="data_estimate") == 1

    def test_estimate_daily_burn(self, recorder: SimulationRecorder) -> None:
        mock_conn = MagicMock()
        mock_conn.__class__.__name__ = "MockConnector"
        mock_conn.get_subscription_cost = MagicMock(return_value={"monthly": 30.0, "daily": 1.0})
        est = CostEstimator(recorder=recorder)
        result = est.estimate_daily_burn(
            connectors=[mock_conn],
            trades_per_day=10,
            avg_notional=100.0,
            exchange="binance",
            llm_calls=20,
            llm_model="gpt-3.5-turbo",
            llm_tokens_in=1000,
            llm_tokens_out=500,
            compute_hours=24.0,
            compute_type="cpu-small",
        )
        assert isinstance(result, SimulatedData)
        assert result.data_type == "daily_burn_estimate"
        breakdown = result.payload["breakdown"]
        assert breakdown["data"] == pytest.approx(1.0)
        assert breakdown["trading"] == pytest.approx(1.0)  # 10 * 100 * 0.001
        assert breakdown["llm"] > 0
        assert breakdown["compute"] == pytest.approx(1.2)  # 24 * 0.05
        assert result.payload["total"] == pytest.approx(breakdown["data"] + breakdown["trading"] + breakdown["llm"] + breakdown["compute"])
        assert recorder.get_event_count(category="cost", action="daily_burn_estimate") == 1

    def test_sim_time_with_clock(self, clock: SimulationClock) -> None:
        est = CostEstimator(clock=clock)
        assert est._sim_time() == clock.now()

    def test_sim_time_without_clock(self) -> None:
        est = CostEstimator()
        before = datetime.now(timezone.utc)
        t = est._sim_time()
        after = datetime.now(timezone.utc)
        assert before <= t <= after
