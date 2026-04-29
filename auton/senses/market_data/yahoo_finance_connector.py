"""Yahoo Finance public API connector (free, no API key required)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from auton.senses.base_connector import BaseConnector
from auton.senses.dataclasses import Candle, MarketData, OrderBook


class YahooFinanceConnector(BaseConnector):
    """Connector for Yahoo Finance public query endpoints.

    Uses the free ``query1.finance.yahoo.com`` API for basic quote data.
    No API key is required.
    """

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

    # Mapping of common crypto symbols to Yahoo Finance tickers
    _SYMBOL_MAP: dict[str, str] = {
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
        "BNB": "BNB-USD",
        "SOL": "SOL-USD",
        "BTCUSDT": "BTC-USD",
        "ETHUSDT": "ETH-USD",
        "BNBUSDT": "BNB-USD",
        "SOLUSDT": "SOL-USD",
    }

    def __init__(
        self,
        event_bus: Any | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self._base_url = base_url or self.BASE_URL
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        async with self._lock:
            if self._connected:
                return
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(30.0),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            self._connected = True

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None
            self._connected = False

    async def fetch_data(self, params: dict[str, Any]) -> dict[str, Any]:
        endpoint = params.get("endpoint", "")
        query = params.get("query", {})
        if self._client is None:
            raise RuntimeError("Connector is not connected. Call connect() first.")
        response = await self._client.get(endpoint, params=query)
        response.raise_for_status()
        data = response.json()
        await self._emit_data({"endpoint": endpoint, "params": params, "result": data})
        return data

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def get_ticker(self, symbol: str) -> MarketData:
        """Fetch the current price and 24h stats for a symbol."""
        ticker = self._symbol_to_ticker(symbol)
        raw = await self.fetch_data(
            {
                "endpoint": f"/{ticker}",
                "query": {"interval": "1d", "range": "1d"},
            }
        )
        result = raw.get("chart", {}).get("result", [{}])[0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice", 0.0)
        prev_close = meta.get("previousClose", price)
        change = price - prev_close
        pct_change = (change / prev_close * 100) if prev_close else 0.0

        return MarketData(
            source="yahoo_finance",
            symbol=symbol.upper(),
            data={
                "price": price,
                "previous_close": prev_close,
                "change": change,
                "change_percent": pct_change,
                "volume": meta.get("regularMarketVolume", 0.0),
            },
            timestamp=datetime.now(timezone.utc),
            metadata={"ticker": ticker},
        )

    async def get_orderbook(self, symbol: str, limit: int = 100) -> OrderBook:
        """Yahoo Finance does not provide order book data; return empty structure."""
        return OrderBook(
            source="yahoo_finance",
            symbol=symbol.upper(),
            bids=[],
            asks=[],
            timestamp=datetime.now(timezone.utc),
            metadata={"note": "order_book_not_supported"},
        )

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1d",
        limit: int = 30,
    ) -> list[Candle]:
        """Fetch historical OHLC data from Yahoo Finance.

        Args:
            symbol: Trading pair, e.g. ``BTCUSDT``.
            interval: Candle interval (``1m``, ``5m``, ``1h``, ``1d``, ``1wk``).
            limit: Number of candles.
        """
        ticker = self._symbol_to_ticker(symbol)
        # Yahoo uses period strings for range
        range_map = {
            "1m": "1d",
            "5m": "5d",
            "1h": "1mo",
            "1d": "1y",
            "1wk": "5y",
        }
        range_val = range_map.get(interval, "1y")
        raw = await self.fetch_data(
            {
                "endpoint": f"/{ticker}",
                "query": {"interval": interval, "range": range_val},
            }
        )
        result = raw.get("chart", {}).get("result", [{}])[0]
        timestamps = result.get("timestamp", [])
        ohlc = result.get("indicators", {}).get("quote", [{}])[0]
        opens = ohlc.get("open", [])
        highs = ohlc.get("high", [])
        lows = ohlc.get("low", [])
        closes = ohlc.get("close", [])
        volumes = ohlc.get("volume", [])

        candles: list[Candle] = []
        count = min(len(timestamps), len(opens), len(highs), len(lows), len(closes), limit)
        for i in range(count):
            if opens[i] is None:
                continue
            candles.append(
                Candle(
                    source="yahoo_finance",
                    symbol=symbol.upper(),
                    interval=interval,
                    open=opens[i],
                    high=highs[i],
                    low=lows[i],
                    close=closes[i],
                    volume=volumes[i] if i < len(volumes) else 0.0,
                    timestamp=datetime.fromtimestamp(timestamps[i], tz=timezone.utc),
                    metadata={},
                )
            )
        return candles

    # ------------------------------------------------------------------
    # Cost & tier
    # ------------------------------------------------------------------

    def get_subscription_cost(self) -> dict[str, float]:
        return {"monthly": 0.0, "daily": 0.0}

    def is_available(self, tier: int) -> bool:
        return tier >= 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _symbol_to_ticker(self, symbol: str) -> str:
        """Map a trading symbol to a Yahoo Finance ticker."""
        upper = symbol.upper()
        if upper in self._SYMBOL_MAP:
            return self._SYMBOL_MAP[upper]
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH"):
            if upper.endswith(quote):
                base = upper[: -len(quote)]
                return self._SYMBOL_MAP.get(base, f"{base}-USD")
        return upper
