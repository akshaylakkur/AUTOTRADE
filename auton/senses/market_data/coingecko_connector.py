"""CoinGecko public API connector (free, no API key required for basic endpoints)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from auton.senses.base_connector import BaseConnector
from auton.senses.dataclasses import Candle, MarketData, OrderBook


class CoinGeckoConnector(BaseConnector):
    """Connector for CoinGecko public REST API.

    Uses the free tier endpoints (no API key required):
      https://api.coingecko.com/api/v3/
    """

    BASE_URL = "https://api.coingecko.com/api/v3"

    # Mapping of common symbols to CoinGecko IDs
    _SYMBOL_MAP: dict[str, str] = {
        "BTC": "bitcoin",
        "ETH": "ethereum",
        "BNB": "binancecoin",
        "SOL": "solana",
        "BTCUSDT": "bitcoin",
        "ETHUSDT": "ethereum",
        "BNBUSDT": "binancecoin",
        "SOLUSDT": "solana",
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
        """Fetch the current price for a symbol."""
        coin_id = self._symbol_to_id(symbol)
        raw = await self.fetch_data(
            {
                "endpoint": "/simple/price",
                "query": {
                    "ids": coin_id,
                    "vs_currencies": "usd",
                    "include_24hr_vol": "true",
                    "include_24hr_change": "true",
                },
            }
        )
        coin_data = raw.get(coin_id, {})
        return MarketData(
            source="coingecko",
            symbol=symbol.upper(),
            data={
                "price": coin_data.get("usd", 0.0),
                "volume_24h": coin_data.get("usd_24h_vol", 0.0),
                "change_24h": coin_data.get("usd_24h_change", 0.0),
            },
            timestamp=datetime.now(timezone.utc),
            metadata={"endpoint": "/simple/price", "coin_id": coin_id},
        )

    async def get_orderbook(self, symbol: str, limit: int = 100) -> OrderBook:
        """CoinGecko does not provide order book data; return empty structure."""
        return OrderBook(
            source="coingecko",
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
        """Fetch historical OHLC data from CoinGecko market chart endpoint.

        Args:
            symbol: Trading pair, e.g. ``BTCUSDT``.
            interval: ``1d`` or ``30d`` (CoinGecko free tier granularity).
            limit: Number of data points.
        """
        coin_id = self._symbol_to_id(symbol)
        days = limit if interval in ("1d", "daily") else 30
        raw = await self.fetch_data(
            {
                "endpoint": f"/coins/{coin_id}/market_chart",
                "query": {"vs_currency": "usd", "days": str(days)},
            }
        )
        prices = raw.get("prices", [])
        market_caps = raw.get("market_caps", [])
        total_volumes = raw.get("total_volumes", [])

        candles: list[Candle] = []
        for i, (ts, price) in enumerate(prices):
            if i == 0:
                continue
            prev_price = prices[i - 1][1]
            open_price = prev_price
            close_price = price
            high_price = max(open_price, close_price)
            low_price = min(open_price, close_price)
            volume = total_volumes[i][1] if i < len(total_volumes) else 0.0
            candles.append(
                Candle(
                    source="coingecko",
                    symbol=symbol.upper(),
                    interval=interval,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                    timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    metadata={"market_cap": market_caps[i][1] if i < len(market_caps) else 0.0},
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

    def _symbol_to_id(self, symbol: str) -> str:
        """Map a trading symbol to a CoinGecko coin ID."""
        upper = symbol.upper()
        if upper in self._SYMBOL_MAP:
            return self._SYMBOL_MAP[upper]
        # Strip common quote currencies
        for quote in ("USDT", "USDC", "BUSD", "BTC", "ETH"):
            if upper.endswith(quote):
                base = upper[: -len(quote)]
                return self._SYMBOL_MAP.get(base, base.lower())
        return upper.lower()
