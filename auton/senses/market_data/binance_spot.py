"""Binance Spot market data connector."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from auton.senses.base_connector import BaseConnector
from auton.senses.dataclasses import Candle, MarketData, OrderBook


class BinanceSpotConnector(BaseConnector):
    """Connector for Binance Spot public REST API.

    All REST endpoints are free to use (tier 0 available).
    """

    BASE_URL = "https://api.binance.com"

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
        """Fetch the 24h ticker for a symbol."""
        raw = await self.fetch_data(
            {"endpoint": "/api/v3/ticker/24hr", "query": {"symbol": symbol.upper()}}
        )
        return MarketData(
            source="binance_spot",
            symbol=symbol.upper(),
            data=raw,
            timestamp=datetime.now(timezone.utc),
            metadata={"endpoint": "/api/v3/ticker/24hr"},
        )

    async def get_orderbook(self, symbol: str, limit: int = 100) -> OrderBook:
        """Fetch the order book (depth) for a symbol."""
        raw = await self.fetch_data(
            {
                "endpoint": "/api/v3/depth",
                "query": {"symbol": symbol.upper(), "limit": limit},
            }
        )
        bids = [
            (float(price), float(qty))
            for price, qty in raw.get("bids", [])
        ]
        asks = [
            (float(price), float(qty))
            for price, qty in raw.get("asks", [])
        ]
        return OrderBook(
            source="binance_spot",
            symbol=symbol.upper(),
            bids=bids,
            asks=asks,
            timestamp=datetime.now(timezone.utc),
            metadata={"lastUpdateId": raw.get("lastUpdateId")},
        )

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch kline/candlestick data for a symbol.

        Args:
            symbol: Trading pair, e.g. ``BTCUSDT``.
            interval: Kline interval, e.g. ``1m``, ``1h``, ``1d``.
            limit: Number of candles (max 1000).
        """
        raw = await self.fetch_data(
            {
                "endpoint": "/api/v3/klines",
                "query": {
                    "symbol": symbol.upper(),
                    "interval": interval,
                    "limit": limit,
                },
            }
        )
        candles: list[Candle] = []
        for item in raw:
            # Binance kline format:
            # [open_time, open, high, low, close, volume, close_time, ...]
            candles.append(
                Candle(
                    source="binance_spot",
                    symbol=symbol.upper(),
                    interval=interval,
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                    timestamp=datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc),
                    metadata={"close_time": item[6]},
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
