"""Comprehensive tests for the Senses data ingestion framework."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from auton.senses.base_connector import BaseConnector, DataReceived
from auton.senses.dataclasses import Candle, MarketData, OrderBook, SentimentScore
from auton.senses.market_data.binance_spot import BinanceSpotConnector
from auton.senses.market_data.coinbase_pro import CoinbaseProConnector
from auton.senses.sentiment.twitter import TwitterSentimentConnector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_event_bus() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def binance_connector(mock_event_bus: AsyncMock) -> BinanceSpotConnector:
    return BinanceSpotConnector(event_bus=mock_event_bus, base_url="https://test.binance")


@pytest.fixture
def coinbase_connector(mock_event_bus: AsyncMock) -> CoinbaseProConnector:
    return CoinbaseProConnector(
        event_bus=mock_event_bus, base_url="https://test.coinbase"
    )


@pytest.fixture
def twitter_connector(mock_event_bus: AsyncMock) -> TwitterSentimentConnector:
    return TwitterSentimentConnector(event_bus=mock_event_bus)


# ---------------------------------------------------------------------------
# BaseConnector
# ---------------------------------------------------------------------------


class MinimalConnector(BaseConnector):
    """Concrete subclass for testing abstract base."""

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def fetch_data(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params}

    def get_subscription_cost(self) -> dict[str, float]:
        return {"monthly": 5.0, "daily": 0.17}

    def is_available(self, tier: int) -> bool:
        return tier >= 1


@pytest.mark.asyncio
async def test_base_connector_lifecycle() -> None:
    conn = MinimalConnector()
    assert not conn.connected
    await conn.connect()
    assert conn.connected
    await conn.disconnect()
    assert not conn.connected


@pytest.mark.asyncio
async def test_base_connector_cost_tracking() -> None:
    conn = MinimalConnector()
    assert conn.lifetime_cost == 0.0
    conn._track_cost(1.5)
    assert conn.lifetime_cost == 1.5
    conn._track_cost(0.5)
    assert conn.lifetime_cost == 2.0


@pytest.mark.asyncio
async def test_base_connector_emits_event(mock_event_bus: AsyncMock) -> None:
    conn = MinimalConnector(event_bus=mock_event_bus)
    await conn.connect()
    payload = {"test": "value"}
    await conn._emit_data(payload)
    mock_event_bus.emit.assert_awaited_once()
    event = mock_event_bus.emit.call_args[0][0]
    assert isinstance(event, DataReceived)
    assert event.connector == "MinimalConnector"
    assert event.payload == payload


@pytest.mark.asyncio
async def test_base_connector_no_event_bus() -> None:
    conn = MinimalConnector(event_bus=None)
    await conn.connect()
    # Should not raise even without an event bus
    await conn._emit_data({"foo": "bar"})


# ---------------------------------------------------------------------------
# BinanceSpotConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_binance_connect_disconnect(binance_connector: BinanceSpotConnector) -> None:
    assert not binance_connector.connected
    await binance_connector.connect()
    assert binance_connector.connected
    assert binance_connector._client is not None
    await binance_connector.disconnect()
    assert not binance_connector.connected
    assert binance_connector._client is None


@pytest.mark.asyncio
async def test_binance_double_connect(binance_connector: BinanceSpotConnector) -> None:
    await binance_connector.connect()
    client = binance_connector._client
    await binance_connector.connect()
    assert binance_connector._client is client


@pytest.mark.asyncio
async def test_binance_fetch_not_connected() -> None:
    conn = BinanceSpotConnector()
    with pytest.raises(RuntimeError, match="not connected"):
        await conn.fetch_data({"endpoint": "/test"})


@pytest.mark.asyncio
async def test_binance_cost_and_tier(binance_connector: BinanceSpotConnector) -> None:
    cost = binance_connector.get_subscription_cost()
    assert cost == {"monthly": 0.0, "daily": 0.0}
    assert binance_connector.is_available(0)
    assert binance_connector.is_available(1)


@pytest.mark.asyncio
async def test_binance_get_ticker(binance_connector: BinanceSpotConnector, mock_event_bus: AsyncMock) -> None:
    await binance_connector.connect()
    mock_response = {
        "symbol": "BTCUSDT",
        "priceChange": "100.0",
        "priceChangePercent": "0.5",
        "lastPrice": "21000.0",
    }
    # Patch client.get
    binance_connector._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(200, json=mock_response)
    )
    result = await binance_connector.get_ticker("btcusdt")
    assert isinstance(result, MarketData)
    assert result.source == "binance_spot"
    assert result.symbol == "BTCUSDT"
    assert result.data["lastPrice"] == "21000.0"
    assert result.timestamp <= datetime.now(timezone.utc)
    mock_event_bus.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_binance_get_orderbook(binance_connector: BinanceSpotConnector, mock_event_bus: AsyncMock) -> None:
    await binance_connector.connect()
    mock_response = {
        "lastUpdateId": 12345,
        "bids": [["21000.0", "1.5"], ["20999.0", "0.5"]],
        "asks": [["21001.0", "2.0"], ["21002.0", "1.0"]],
    }
    binance_connector._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(200, json=mock_response)
    )
    result = await binance_connector.get_orderbook("BTCUSDT", limit=5)
    assert isinstance(result, OrderBook)
    assert result.symbol == "BTCUSDT"
    assert result.bids == [(21000.0, 1.5), (20999.0, 0.5)]
    assert result.asks == [(21001.0, 2.0), (21002.0, 1.0)]
    assert result.metadata["lastUpdateId"] == 12345


@pytest.mark.asyncio
async def test_binance_get_klines(binance_connector: BinanceSpotConnector) -> None:
    await binance_connector.connect()
    mock_response = [
        [1609459200000, "29000.0", "29500.0", "28800.0", "29200.0", "100.0", 1609545600000, "200.0"],
        [1609545600000, "29200.0", "30000.0", "29100.0", "29800.0", "150.0", 1609632000000, "300.0"],
    ]
    binance_connector._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(200, json=mock_response)
    )
    candles = await binance_connector.get_klines("BTCUSDT", "1d", limit=2)
    assert len(candles) == 2
    assert isinstance(candles[0], Candle)
    assert candles[0].source == "binance_spot"
    assert candles[0].symbol == "BTCUSDT"
    assert candles[0].open == 29000.0
    assert candles[0].high == 29500.0
    assert candles[0].low == 28800.0
    assert candles[0].close == 29200.0
    assert candles[0].volume == 100.0


# ---------------------------------------------------------------------------
# CoinbaseProConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coinbase_connect_disconnect(coinbase_connector: CoinbaseProConnector) -> None:
    assert not coinbase_connector.connected
    await coinbase_connector.connect()
    assert coinbase_connector.connected
    await coinbase_connector.disconnect()
    assert not coinbase_connector.connected


@pytest.mark.asyncio
async def test_coinbase_fetch_not_connected() -> None:
    conn = CoinbaseProConnector()
    with pytest.raises(RuntimeError, match="not connected"):
        await conn.fetch_data({"endpoint": "/test"})


@pytest.mark.asyncio
async def test_coinbase_cost_and_tier(coinbase_connector: CoinbaseProConnector) -> None:
    cost = coinbase_connector.get_subscription_cost()
    assert cost == {"monthly": 0.0, "daily": 0.0}
    assert coinbase_connector.is_available(0)


@pytest.mark.asyncio
async def test_coinbase_get_products(coinbase_connector: CoinbaseProConnector, mock_event_bus: AsyncMock) -> None:
    await coinbase_connector.connect()
    mock_response = [{"id": "BTC-USD", "display_name": "BTC/USD"}]
    coinbase_connector._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(200, json=mock_response)
    )
    products = await coinbase_connector.get_products()
    assert products == mock_response
    mock_event_bus.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_coinbase_get_ticker(coinbase_connector: CoinbaseProConnector) -> None:
    await coinbase_connector.connect()
    mock_response = {"trade_id": 1, "price": "35000.00", "size": "0.01"}
    coinbase_connector._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(200, json=mock_response)
    )
    result = await coinbase_connector.get_ticker("BTC-USD")
    assert isinstance(result, MarketData)
    assert result.symbol == "BTC-USD"
    assert result.data["price"] == "35000.00"


@pytest.mark.asyncio
async def test_coinbase_get_candles(coinbase_connector: CoinbaseProConnector) -> None:
    await coinbase_connector.connect()
    mock_response = [
        [1609459200, 28800.0, 29500.0, 29000.0, 29200.0, 100.0],
        [1609545600, 29100.0, 30000.0, 29200.0, 29800.0, 150.0],
    ]
    coinbase_connector._client.get = AsyncMock(  # type: ignore[method-assign]
        return_value=_mock_response(200, json=mock_response)
    )
    candles = await coinbase_connector.get_candles("BTC-USD", granularity=3600)
    assert len(candles) == 2
    assert candles[0].source == "coinbase_pro"
    assert candles[0].symbol == "BTC-USD"
    assert candles[0].open == 29000.0
    assert candles[0].high == 29500.0
    assert candles[0].low == 28800.0
    assert candles[0].close == 29200.0
    assert candles[0].volume == 100.0


# ---------------------------------------------------------------------------
# TwitterSentimentConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_twitter_connect_disconnect(twitter_connector: TwitterSentimentConnector) -> None:
    assert not twitter_connector.connected
    await twitter_connector.connect()
    assert twitter_connector.connected
    await twitter_connector.disconnect()
    assert not twitter_connector.connected


@pytest.mark.asyncio
async def test_twitter_cost_and_tier(twitter_connector: TwitterSentimentConnector) -> None:
    cost = twitter_connector.get_subscription_cost()
    assert cost == {"monthly": 99.0, "daily": 3.3}
    assert not twitter_connector.is_available(0)
    assert not twitter_connector.is_available(1)
    assert twitter_connector.is_available(2)
    assert twitter_connector.is_available(3)


@pytest.mark.asyncio
async def test_twitter_search_tweets(twitter_connector: TwitterSentimentConnector) -> None:
    await twitter_connector.connect()
    scores = await twitter_connector.search_tweets("bitcoin", max_results=5)
    assert len(scores) == 5
    for s in scores:
        assert isinstance(s, SentimentScore)
        assert s.source == "twitter_skeleton"
        assert s.query == "bitcoin"
        assert s.metadata["mock"] is True


@pytest.mark.asyncio
async def test_twitter_get_sentiment_score_batch(twitter_connector: TwitterSentimentConnector) -> None:
    texts = ["This is amazing!", "This is terrible!", "Neutral text here."]
    result = twitter_connector.get_sentiment_score(texts)
    assert isinstance(result, SentimentScore)
    assert result.query == "batch"
    assert -1.0 <= result.score <= 1.0
    assert result.magnitude >= 0.0


@pytest.mark.asyncio
async def test_twitter_get_sentiment_score_empty(twitter_connector: TwitterSentimentConnector) -> None:
    result = twitter_connector.get_sentiment_score([])
    assert result.score == 0.0
    assert result.magnitude == 0.0


def test_twitter_keyword_scorer_positive(twitter_connector: TwitterSentimentConnector) -> None:
    score = twitter_connector._keyword_score("Bitcoin is going to the moon! Great profit and strong buy signal.")
    assert score > 0.0


def test_twitter_keyword_scorer_negative(twitter_connector: TwitterSentimentConnector) -> None:
    score = twitter_connector._keyword_score("Market crash! Terrible dump and panic sell.")
    assert score < 0.0


def test_twitter_keyword_scorer_neutral(twitter_connector: TwitterSentimentConnector) -> None:
    score = twitter_connector._keyword_score("The weather is cloudy today.")
    assert score == 0.0


@pytest.mark.asyncio
async def test_twitter_fetch_data(twitter_connector: TwitterSentimentConnector, mock_event_bus: AsyncMock) -> None:
    await twitter_connector.connect()
    result = await twitter_connector.fetch_data({"query": "ethereum", "max_results": 3})
    assert "tweets" in result
    assert len(result["tweets"]) == 3
    assert result["query"] == "ethereum"
    mock_event_bus.emit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


def _mock_response(status: int, json: Any, url: str = "http://test") -> httpx.Response:
    request = httpx.Request("GET", url)
    return httpx.Response(status, json=json, request=request)


def test_market_data_defaults() -> None:
    md = MarketData(source="test", symbol="BTCUSD", data={"price": 1.0})
    assert md.timestamp <= datetime.now(timezone.utc)
    assert md.metadata == {}


def test_orderbook_immutable() -> None:
    ob = OrderBook(source="test", symbol="BTCUSD", bids=[(1.0, 2.0)], asks=[])
    with pytest.raises(FrozenInstanceError):
        ob.symbol = "ETHUSD"  # type: ignore[misc]


def test_candle_immutable() -> None:
    c = Candle(
        source="test",
        symbol="BTCUSD",
        interval="1h",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
    )
    with pytest.raises(FrozenInstanceError):
        c.close = 2.0  # type: ignore[misc]


def test_sentiment_score_defaults() -> None:
    ss = SentimentScore(source="test", query="foo", score=0.5)
    assert ss.magnitude == 0.0
    assert ss.metadata == {}


# ---------------------------------------------------------------------------
# Integration / concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_connect_disconnect() -> None:
    """Ensure lock-based connect/disconnect is safe under concurrency."""
    conn = BinanceSpotConnector(base_url="https://test.binance")

    async def connect_disconnect() -> None:
        await conn.connect()
        await asyncio.sleep(0)
        await conn.disconnect()

    await asyncio.gather(*[connect_disconnect() for _ in range(10)])
    assert not conn.connected
