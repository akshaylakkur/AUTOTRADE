"""Coinbase Pro (Coinbase Advanced Trade) market data connector."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from auton.senses.base_connector import BaseConnector
from auton.senses.dataclasses import Candle, MarketData


class CoinbaseProConnector(BaseConnector):
    """Connector for Coinbase Pro / Advanced Trade public REST API.

    Basic REST endpoints are free to use (tier 0 available).
    """

    BASE_URL = "https://api.exchange.coinbase.com"

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

    async def get_products(self) -> list[dict[str, Any]]:
        """Fetch all available trading products."""
        return await self.fetch_data({"endpoint": "/products"})

    async def get_ticker(self, product_id: str) -> MarketData:
        """Fetch the ticker for a single product."""
        raw = await self.fetch_data({"endpoint": f"/products/{product_id}/ticker"})
        return MarketData(
            source="coinbase_pro",
            symbol=product_id.upper(),
            data=raw,
            timestamp=datetime.now(timezone.utc),
            metadata={"endpoint": f"/products/{product_id}/ticker"},
        )

    async def get_candles(
        self,
        product_id: str,
        granularity: int = 3600,
    ) -> list[Candle]:
        """Fetch historic rates (candles) for a product.

        Args:
            product_id: Trading pair, e.g. ``BTC-USD``.
            granularity: Candle granularity in seconds (e.g. 60, 300, 900, 3600, 21600, 86400).
        """
        raw = await self.fetch_data(
            {
                "endpoint": f"/products/{product_id}/candles",
                "query": {"granularity": granularity},
            }
        )
        candles: list[Candle] = []
        for item in raw:
            # Coinbase candle format: [time, low, high, open, close, volume]
            candles.append(
                Candle(
                    source="coinbase_pro",
                    symbol=product_id.upper(),
                    interval=str(granularity),
                    open=float(item[3]),
                    high=float(item[2]),
                    low=float(item[1]),
                    close=float(item[4]),
                    volume=float(item[5]),
                    timestamp=datetime.fromtimestamp(item[0], tz=timezone.utc),
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
