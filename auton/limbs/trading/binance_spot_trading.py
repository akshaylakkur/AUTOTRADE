"""Binance spot trading limb with paper-mode support."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from auton.limbs.base_limb import BaseLimb
from auton.limbs.dataclasses import OrderResult, OrderSide, OrderStatus, OrderType, TradeOrder


class BinanceSpotTradingLimb(BaseLimb):
    """Async limb for Binance spot-market trading.

    Parameters
    ----------
    api_key:
        Binance API key.  If *None* the limb runs in **paper** mode.
    api_secret:
        Binance API secret.  If *None* the limb runs in **paper** mode.
    base_url:
        REST API base URL.  Defaults to the live endpoint;
        override with ``https://testnet.binance.vision`` for testnet.
    paper:
        When ``True`` (default) all orders are simulated locally and no
        real funds are at risk.
    """

    _MAKER_FEE_RATE = 0.001
    _TAKER_FEE_RATE = 0.001

    @classmethod
    def is_configured(cls) -> bool:
        """Return True when Binance API credentials are present in the environment."""
        import os
        return bool(os.environ.get("BINANCE_API_KEY", "").strip() and os.environ.get("BINANCE_SECRET_KEY", "").strip())

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str = "https://api.binance.com",
        paper: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._paper = paper or (api_key is None or api_secret is None)

        # Paper-mode state
        self._paper_balance: dict[str, float] = {"USDT": 10000.0}
        self._paper_orders: dict[str, dict[str, Any]] = {}
        self._paper_order_counter = 0

        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        """Dispatch a generic ``{"method": ..., "kwargs": ...}`` action."""
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "place_order":
            return await self.place_order(**kwargs)
        if method == "cancel_order":
            return await self.cancel_order(**kwargs)
        if method == "get_account_balance":
            return await self.get_account_balance()
        if method == "get_open_orders":
            return await self.get_open_orders(**kwargs)
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        """Estimate trading fee for a place_order action."""
        method = action.get("method")
        if method != "place_order":
            return 0.0
        qty = action.get("kwargs", {}).get("quantity", 0.0)
        price = action.get("kwargs", {}).get("price", 0.0) or 0.0
        notional = qty * price
        return notional * self._TAKER_FEE_RATE

    def is_available(self, tier: int) -> bool:
        return tier >= 0

    async def health_check(self) -> dict[str, Any]:
        try:
            if self._paper:
                return {"status": "healthy", "mode": "paper", "latency_ms": 0.0}
            start = asyncio.get_event_loop().time()
            r = await self._client.get("/api/v3/ping")
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
    # Trading helpers
    # ------------------------------------------------------------------ #

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
    ) -> OrderResult:
        self._emit("limb.order.requested", {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "price": price,
        })

        if self._paper:
            result = await self._paper_place_order(symbol, side, quantity, order_type, price)
        else:
            result = await self._live_place_order(symbol, side, quantity, order_type, price)

        # Ledger charge
        fee = self._estimate_fee(result)
        await self._charge(fee, f"binance_fee:{result.order_id}")

        self._emit("limb.order.executed", {
            "order_id": result.order_id,
            "symbol": result.symbol,
            "status": result.status.value,
            "fee": fee,
        })
        return result

    async def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        self._emit("limb.order.cancel_requested", {"symbol": symbol, "order_id": order_id})
        if self._paper:
            return await self._paper_cancel_order(symbol, order_id)
        return await self._live_cancel_order(symbol, order_id)

    async def get_account_balance(self) -> dict[str, Any]:
        if self._paper:
            return {"balances": [{"asset": k, "free": v, "locked": 0.0} for k, v in self._paper_balance.items()]}
        return await self._signed_get("/api/v3/account")

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if self._paper:
            orders = list(self._paper_orders.values())
            if symbol:
                orders = [o for o in orders if o.get("symbol") == symbol]
            return orders
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return await self._signed_get("/api/v3/openOrders", params=params)

    # ------------------------------------------------------------------ #
    # Paper-mode implementations
    # ------------------------------------------------------------------ #

    async def _paper_place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None,
    ) -> OrderResult:
        await asyncio.sleep(0)  # yield control for realism
        self._paper_order_counter += 1
        order_id = f"PAPER-{self._paper_order_counter}"
        ts = int(time.time() * 1000)

        # simplistic fill logic
        fill_price = price or 100.0
        fee = fill_price * quantity * self._TAKER_FEE_RATE

        base, quote = self._split_pair(symbol)
        if side.upper() == "BUY":
            cost = fill_price * quantity + fee
            self._paper_balance[quote] = self._paper_balance.get(quote, 0.0) - cost
            self._paper_balance[base] = self._paper_balance.get(base, 0.0) + quantity
        else:
            proceeds = fill_price * quantity - fee
            self._paper_balance[base] = self._paper_balance.get(base, 0.0) - quantity
            self._paper_balance[quote] = self._paper_balance.get(quote, 0.0) + proceeds

        raw = {
            "orderId": order_id,
            "symbol": symbol,
            "side": side,
            "status": "FILLED",
            "executedQty": str(quantity),
            "cummulativeQuoteQty": str(fill_price * quantity),
            "price": str(fill_price),
            "type": order_type,
            "transactTime": ts,
        }
        self._paper_orders[order_id] = raw
        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            side=OrderSide(side.upper()),
            status=OrderStatus.FILLED,
            executed_qty=quantity,
            cummulative_quote_qty=fill_price * quantity,
            price=fill_price,
            order_type=OrderType(order_type.upper()),
            fills=[{"price": str(fill_price), "qty": str(quantity), "commission": str(fee)}],
            raw_response=raw,
        )

    async def _paper_cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        if order_id not in self._paper_orders:
            raise ValueError(f"Order {order_id} not found")
        self._paper_orders[order_id]["status"] = "CANCELED"
        return {"orderId": order_id, "symbol": symbol, "status": "CANCELED"}

    # ------------------------------------------------------------------ #
    # Live-mode implementations
    # ------------------------------------------------------------------ #

    async def _live_place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None,
    ) -> OrderResult:
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
            "timestamp": int(time.time() * 1000),
        }
        if price is not None:
            params["price"] = price
            params["timeInForce"] = "GTC"

        raw = await self._signed_post("/api/v3/order", params)
        return _raw_to_order_result(raw)

    async def _live_cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        params = {
            "symbol": symbol.upper(),
            "orderId": order_id,
            "timestamp": int(time.time() * 1000),
        }
        return await self._signed_delete("/api/v3/order", params)

    # ------------------------------------------------------------------ #
    # HTTP helpers
    # ------------------------------------------------------------------ #

    async def _signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        r = await self._client.get(path, params=params, headers={"X-MBX-APIKEY": self._api_key})
        r.raise_for_status()
        return r.json()

    async def _signed_post(self, path: str, params: dict[str, Any]) -> Any:
        params["signature"] = self._sign(params)
        r = await self._client.post(
            path,
            data=params,
            headers={"X-MBX-APIKEY": self._api_key},
        )
        r.raise_for_status()
        return r.json()

    async def _signed_delete(self, path: str, params: dict[str, Any]) -> Any:
        params["signature"] = self._sign(params)
        r = await self._client.delete(
            path,
            params=params,
            headers={"X-MBX-APIKEY": self._api_key},
        )
        r.raise_for_status()
        return r.json()

    def _sign(self, params: dict[str, Any]) -> str:
        query = urlencode(params)
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------------------ #
    # Utils
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_pair(symbol: str) -> tuple[str, str]:
        # naive split for major stable-coin pairs
        for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH"):
            if symbol.upper().endswith(quote):
                return symbol.upper()[: -len(quote)], quote
        raise ValueError(f"Cannot split symbol: {symbol}")

    def _estimate_fee(self, result: OrderResult) -> float:
        return result.cummulative_quote_qty * self._TAKER_FEE_RATE

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BinanceSpotTradingLimb:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


def _raw_to_order_result(raw: dict[str, Any]) -> OrderResult:
    return OrderResult(
        order_id=str(raw.get("orderId", raw.get("clientOrderId", ""))),
        symbol=raw.get("symbol", ""),
        side=OrderSide(raw.get("side", "BUY")),
        status=OrderStatus(raw.get("status", "NEW")),
        executed_qty=float(raw.get("executedQty", 0)),
        cummulative_quote_qty=float(raw.get("cummulativeQuoteQty", 0)),
        price=float(raw.get("price", 0)),
        order_type=OrderType(raw.get("type", "MARKET")),
        fills=raw.get("fills", []),
        raw_response=raw,
    )
