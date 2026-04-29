"""MarketDataSimulator — real market data, simulated trades."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from auton.limbs.dataclasses import OrderResult
from auton.limbs.trading.binance_spot_trading import BinanceSpotTradingLimb
from auton.senses.base_connector import BaseConnector
from auton.senses.dataclasses import Candle, MarketData, OrderBook
from auton.simulation.clock import SimulationClock
from auton.simulation.connectors.dataclasses import SimulatedData
from auton.simulation.recorder import SimulationRecorder
from auton.simulation.wallet import SimulatedWallet


class MarketDataSimulator:
    """Wraps production market data connectors and a paper-trading limb.

    Fetches real historical or live market data through production
    :class:`BaseConnector` instances, wraps every payload in
    :class:`SimulatedData`, and routes trade execution to a
    :class:`BinanceSpotTradingLimb` running in paper mode.

    All trading fees are debited from the supplied
    :class:`SimulatedWallet` and recorded via
    :class:`SimulationRecorder`.
    """

    def __init__(
        self,
        connectors: dict[str, BaseConnector] | None = None,
        trading_limb: BinanceSpotTradingLimb | None = None,
        wallet: SimulatedWallet | None = None,
        recorder: SimulationRecorder | None = None,
        clock: SimulationClock | None = None,
    ) -> None:
        self._connectors: dict[str, BaseConnector] = connectors or {}
        self._trading_limb = trading_limb or BinanceSpotTradingLimb(paper=True)
        self._wallet = wallet
        self._recorder = recorder
        self._clock = clock

    # ------------------------------------------------------------------
    # Market data helpers
    # ------------------------------------------------------------------

    async def get_ticker(self, connector_name: str, symbol: str) -> SimulatedData:
        """Fetch a 24h ticker via *connector_name* and wrap as simulated."""
        conn = self._connectors.get(connector_name)
        if conn is None:
            raise ValueError(f"Unknown connector: {connector_name}")

        if not conn.connected:
            await conn.connect()

        market_data: MarketData = await conn.get_ticker(symbol)
        sim = SimulatedData(
            source=connector_name,
            data_type="ticker",
            payload=market_data,
            sim_time=self._sim_time(),
            metadata={"symbol": symbol.upper()},
        )
        self._record("market_data", "ticker_fetched", sim)
        return sim

    async def get_klines(
        self,
        connector_name: str,
        symbol: str,
        interval: str,
        limit: int = 500,
    ) -> SimulatedData:
        """Fetch klines/candles via *connector_name* and wrap as simulated."""
        conn = self._connectors.get(connector_name)
        if conn is None:
            raise ValueError(f"Unknown connector: {connector_name}")

        if not conn.connected:
            await conn.connect()

        candles: list[Candle]
        if hasattr(conn, "get_klines"):
            candles = await conn.get_klines(symbol, interval, limit)
        elif hasattr(conn, "get_candles"):
            candles = await conn.get_candles(symbol, interval)  # type: ignore[arg-type]
        else:
            raise RuntimeError(f"Connector {connector_name} does not support klines/candles")

        sim = SimulatedData(
            source=connector_name,
            data_type="klines",
            payload=candles,
            sim_time=self._sim_time(),
            metadata={"symbol": symbol.upper(), "interval": interval, "count": len(candles)},
        )
        self._record("market_data", "klines_fetched", sim)
        return sim

    async def get_orderbook(
        self,
        connector_name: str,
        symbol: str,
        limit: int = 100,
    ) -> SimulatedData:
        """Fetch an order-book snapshot via *connector_name* and wrap as simulated."""
        conn = self._connectors.get(connector_name)
        if conn is None:
            raise ValueError(f"Unknown connector: {connector_name}")

        if not conn.connected:
            await conn.connect()

        if not hasattr(conn, "get_orderbook"):
            raise RuntimeError(f"Connector {connector_name} does not support orderbook")

        orderbook: OrderBook = await conn.get_orderbook(symbol, limit)
        sim = SimulatedData(
            source=connector_name,
            data_type="orderbook",
            payload=orderbook,
            sim_time=self._sim_time(),
            metadata={"symbol": symbol.upper(), "limit": limit},
        )
        self._record("market_data", "orderbook_fetched", sim)
        return sim

    # ------------------------------------------------------------------
    # Simulated trading
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
    ) -> OrderResult:
        """Execute a paper order and debit the simulated wallet for fees."""
        result: OrderResult = await self._trading_limb.place_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
        )

        fee = self._trading_limb._estimate_fee(result)
        if self._wallet is not None:
            self._wallet.debit(fee, f"sim_trading_fee:{result.order_id}")

        self._record(
            "trade",
            "order_placed",
            {
                "order_id": result.order_id,
                "symbol": result.symbol,
                "side": result.side.value,
                "status": result.status.value,
                "fee": fee,
                "simulated": True,
            },
        )
        return result

    async def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Cancel a paper order."""
        result = await self._trading_limb.cancel_order(symbol, order_id)
        self._record("trade", "order_cancelled", {"symbol": symbol, "order_id": order_id})
        return result

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Return open paper orders."""
        return await self._trading_limb.get_open_orders(symbol)

    async def get_account_balance(self) -> dict[str, Any]:
        """Return the paper account balance."""
        return await self._trading_limb.get_account_balance()

    async def get_portfolio_value(self, quote_asset: str = "USDT") -> float:
        """Return the total value of the paper portfolio in *quote_asset*.

        This is a simplified valuation that sums the *quote_asset*
        balance plus an estimate of other holdings. In a full
        implementation each position would be marked to market.
        """
        bal = await self._trading_limb.get_account_balance()
        total = 0.0
        for b in bal.get("balances", []):
            free = b.get("free", 0.0)
            locked = b.get("locked", 0.0)
            total += free + locked
        return total

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Disconnect all connectors and close the trading limb."""
        for conn in self._connectors.values():
            if conn.connected:
                await conn.disconnect()
        await self._trading_limb.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sim_time(self) -> datetime:
        if self._clock is not None:
            return self._clock.now()
        return datetime.now(timezone.utc)

    def _record(self, category: str, action: str, payload: dict[str, Any]) -> None:
        if self._recorder is not None:
            self._recorder.record(self._sim_time(), category, action, payload)
