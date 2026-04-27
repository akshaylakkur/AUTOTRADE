"""Comprehensive pytest suite for auton.limbs."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from typing import Any
from unittest.mock import AsyncMock

import pytest

from auton.limbs.base_limb import BaseLimb
from auton.limbs.commerce.stripe_limb import StripeLimb
from auton.limbs.dataclasses import (
    CheckoutSession,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    Product,
    TradeOrder,
)
from auton.limbs.trading.binance_spot_trading import BinanceSpotTradingLimb


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def event_bus():
    bus = AsyncMock()
    bus.emit = AsyncMock()
    return bus


@pytest.fixture
def ledger():
    led = AsyncMock()
    led.charge = AsyncMock()
    return led


@pytest.fixture
def tier_gate():
    tg = AsyncMock()
    return tg


@pytest.fixture
def binance_limb(event_bus, ledger):
    return BinanceSpotTradingLimb(
        event_bus=event_bus,
        ledger=ledger,
        paper=True,
    )


@pytest.fixture
def stripe_limb(event_bus, ledger):
    return StripeLimb(
        event_bus=event_bus,
        ledger=ledger,
        api_key=None,
    )


# --------------------------------------------------------------------------- #
# BaseLimb
# --------------------------------------------------------------------------- #


def test_base_limb_is_abstract():
    with pytest.raises(TypeError):
        BaseLimb()


class DummyLimb(BaseLimb):
    async def execute(self, action: Any) -> Any:
        return action

    async def get_cost_estimate(self, action: Any) -> float:
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 0

    async def health_check(self) -> dict[str, Any]:
        return {"status": "ok"}


def test_dummy_limb_name():
    limb = DummyLimb()
    assert limb.name == "DummyLimb"


@pytest.mark.asyncio
async def test_dummy_limb_execute():
    limb = DummyLimb()
    assert await limb.execute("foo") == "foo"


@pytest.mark.asyncio
async def test_dummy_limb_health():
    limb = DummyLimb()
    assert await limb.health_check() == {"status": "ok"}


def test_dummy_limb_is_available():
    limb = DummyLimb()
    assert limb.is_available(0) is True
    assert limb.is_available(-1) is False


@pytest.mark.asyncio
async def test_base_limb_event_bus(event_bus):
    limb = DummyLimb(event_bus=event_bus)
    limb._emit("test.event", {"key": "value"})
    await asyncio.sleep(0.05)  # let the fire-and-forget task run
    event_bus.emit.assert_awaited_once()
    call_args = event_bus.emit.await_args
    assert call_args[0][0] == "test.event"
    assert call_args[0][1]["limb"] == "DummyLimb"
    assert call_args[0][1]["key"] == "value"


@pytest.mark.asyncio
async def test_base_limb_ledger_charge(ledger):
    limb = DummyLimb(ledger=ledger)
    await limb._charge(1.23, "test_charge")
    ledger.charge.assert_awaited_once_with(1.23, "test_charge", source="DummyLimb")


# --------------------------------------------------------------------------- #
# BinanceSpotTradingLimb — paper mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_binance_is_available():
    limb = BinanceSpotTradingLimb(paper=True)
    assert limb.is_available(0) is True
    assert limb.is_available(1) is True


@pytest.mark.asyncio
async def test_binance_health_check_paper():
    limb = BinanceSpotTradingLimb(paper=True)
    result = await limb.health_check()
    assert result["status"] == "healthy"
    assert result["mode"] == "paper"


@pytest.mark.asyncio
async def test_binance_place_order_paper(binance_limb):
    result = await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.1,
        order_type="MARKET",
    )
    assert isinstance(result, OrderResult)
    assert result.symbol == "BTCUSDT"
    assert result.side == OrderSide.BUY
    assert result.status == OrderStatus.FILLED
    assert result.executed_qty == 0.1


@pytest.mark.asyncio
async def test_binance_place_order_limit_paper(binance_limb):
    result = await binance_limb.place_order(
        symbol="ETHUSDT",
        side="SELL",
        quantity=1.0,
        order_type="LIMIT",
        price=2000.0,
    )
    assert result.order_type == OrderType.LIMIT
    assert result.price == 2000.0


@pytest.mark.asyncio
async def test_binance_cancel_order_paper(binance_limb):
    placed = await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.1,
        order_type="MARKET",
    )
    cancel_result = await binance_limb.cancel_order(
        symbol="BTCUSDT",
        order_id=placed.order_id,
    )
    assert cancel_result["status"] == "CANCELED"


@pytest.mark.asyncio
async def test_binance_cancel_unknown_order_raises(binance_limb):
    with pytest.raises(ValueError, match="not found"):
        await binance_limb.cancel_order("BTCUSDT", "UNKNOWN-999")


@pytest.mark.asyncio
async def test_binance_get_account_balance_paper(binance_limb):
    bal = await binance_limb.get_account_balance()
    assert "balances" in bal
    assets = {b["asset"]: b["free"] for b in bal["balances"]}
    assert "USDT" in assets


@pytest.mark.asyncio
async def test_binance_get_open_orders_paper(binance_limb):
    await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.1,
        order_type="MARKET",
    )
    orders = await binance_limb.get_open_orders("BTCUSDT")
    assert len(orders) == 1
    assert orders[0]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_binance_get_open_orders_all_symbols(binance_limb):
    await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.1,
        order_type="MARKET",
    )
    await binance_limb.place_order(
        symbol="ETHUSDT",
        side="SELL",
        quantity=1.0,
        order_type="MARKET",
    )
    orders = await binance_limb.get_open_orders()
    assert len(orders) == 2


@pytest.mark.asyncio
async def test_binance_cost_estimate():
    limb = BinanceSpotTradingLimb(paper=True)
    cost = await limb.get_cost_estimate(
        {"method": "place_order", "kwargs": {"quantity": 1.0, "price": 100.0}}
    )
    assert cost == pytest.approx(0.1)  # 1.0 * 100.0 * 0.001


@pytest.mark.asyncio
async def test_binance_cost_estimate_non_order():
    limb = BinanceSpotTradingLimb(paper=True)
    cost = await limb.get_cost_estimate({"method": "get_account_balance"})
    assert cost == 0.0


@pytest.mark.asyncio
async def test_binance_execute_action(binance_limb):
    result = await binance_limb.execute(
        {
            "method": "place_order",
            "kwargs": {
                "symbol": "BTCUSDT",
                "side": "BUY",
                "quantity": 0.05,
                "order_type": "MARKET",
            },
        }
    )
    assert isinstance(result, OrderResult)
    assert result.executed_qty == 0.05


@pytest.mark.asyncio
async def test_binance_execute_unknown_action(binance_limb):
    with pytest.raises(ValueError, match="Unknown action"):
        await binance_limb.execute({"method": "foo"})


@pytest.mark.asyncio
async def test_binance_buy_updates_balance(binance_limb):
    await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.1,
        order_type="MARKET",
        price=100.0,
    )
    bal = await binance_limb.get_account_balance()
    assets = {b["asset"]: b["free"] for b in bal["balances"]}
    assert "BTC" in assets
    assert assets["BTC"] == 0.1
    # USDT should be reduced by cost + fee
    assert assets["USDT"] < 10000.0


@pytest.mark.asyncio
async def test_binance_sell_updates_balance(binance_limb):
    # seed BTC
    await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=100.0,
    )
    await binance_limb.place_order(
        symbol="BTCUSDT",
        side="SELL",
        quantity=0.5,
        order_type="MARKET",
        price=100.0,
    )
    bal = await binance_limb.get_account_balance()
    assets = {b["asset"]: b["free"] for b in bal["balances"]}
    assert assets["BTC"] == 0.5


@pytest.mark.asyncio
async def test_binance_ledger_charge_on_place_order(binance_limb, ledger):
    await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=1.0,
        order_type="MARKET",
        price=100.0,
    )
    ledger.charge.assert_awaited()
    assert "binance_fee" in ledger.charge.await_args[0][1]


@pytest.mark.asyncio
async def test_binance_event_bus_emits(binance_limb, event_bus):
    await binance_limb.place_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.1,
        order_type="MARKET",
    )
    await asyncio.sleep(0.05)
    event_bus.emit.assert_awaited()
    # at least one call should be limb.order.executed
    event_types = [call[0][0] for call in event_bus.emit.await_args_list]
    assert "limb.order.executed" in event_types


@pytest.mark.asyncio
async def test_binance_context_manager():
    async with BinanceSpotTradingLimb(paper=True) as limb:
        result = await limb.health_check()
        assert result["status"] == "healthy"


# --------------------------------------------------------------------------- #
# StripeLimb — skeleton / mock mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stripe_is_available():
    limb = StripeLimb(api_key=None)
    assert limb.is_available(0) is False
    assert limb.is_available(1) is True
    assert limb.is_available(2) is True


@pytest.mark.asyncio
async def test_stripe_health_check_skeleton():
    limb = StripeLimb(api_key=None)
    result = await limb.health_check()
    assert result["status"] == "skeleton"
    assert result["mode"] == "mock"


@pytest.mark.asyncio
async def test_stripe_create_product(stripe_limb):
    product = await stripe_limb.create_product(
        name="Test Widget",
        description="A widget for testing",
        price_cents=499,
    )
    assert isinstance(product, Product)
    assert product.name == "Test Widget"
    assert product.price_cents == 499
    assert product.product_id.startswith("mock_prod_")


@pytest.mark.asyncio
async def test_stripe_create_checkout_session(stripe_limb):
    product = await stripe_limb.create_product(
        name="Test Widget", price_cents=499
    )
    session = await stripe_limb.create_checkout_session(
        product_id=product.product_id,
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
    )
    assert isinstance(session, CheckoutSession)
    assert session.product_id == product.product_id
    assert session.status == "open"
    assert session.url.startswith("https://checkout.stripe.com/mock/")


@pytest.mark.asyncio
async def test_stripe_list_products(stripe_limb):
    await stripe_limb.create_product(name="A", price_cents=100)
    await stripe_limb.create_product(name="B", price_cents=200)
    products = await stripe_limb.list_products()
    assert len(products) == 2
    names = {p.name for p in products}
    assert names == {"A", "B"}


@pytest.mark.asyncio
async def test_stripe_empty_list_products(stripe_limb):
    products = await stripe_limb.list_products()
    assert products == []


@pytest.mark.asyncio
async def test_stripe_cost_estimate(stripe_limb):
    cost = await stripe_limb.get_cost_estimate(
        {"method": "create_product", "kwargs": {}}
    )
    assert cost == 0.0


@pytest.mark.asyncio
async def test_stripe_execute_create_product(stripe_limb):
    result = await stripe_limb.execute(
        {
            "method": "create_product",
            "kwargs": {
                "name": "Exec Widget",
                "description": "via execute",
                "price_cents": 999,
            },
        }
    )
    assert isinstance(result, Product)
    assert result.name == "Exec Widget"


@pytest.mark.asyncio
async def test_stripe_execute_unknown_action(stripe_limb):
    with pytest.raises(ValueError, match="Unknown action"):
        await stripe_limb.execute({"method": "foo"})


@pytest.mark.asyncio
async def test_stripe_ledger_charge_on_create(stripe_limb, ledger):
    product = await stripe_limb.create_product(name="ChargeTest", price_cents=100)
    ledger.charge.assert_awaited()
    assert "stripe_product_created" in ledger.charge.await_args[0][1]


@pytest.mark.asyncio
async def test_stripe_event_bus_emits(stripe_limb, event_bus):
    await stripe_limb.create_product(name="EventTest", price_cents=100)
    await asyncio.sleep(0.05)
    event_bus.emit.assert_awaited()
    event_types = [call[0][0] for call in event_bus.emit.await_args_list]
    assert "limb.product.created" in event_types


@pytest.mark.asyncio
async def test_stripe_context_manager():
    async with StripeLimb(api_key=None) as limb:
        result = await limb.health_check()
        assert result["status"] == "skeleton"


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


def test_trade_order_immutable():
    order = TradeOrder(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=1.0,
        order_type=OrderType.MARKET,
    )
    with pytest.raises(FrozenInstanceError):
        order.quantity = 2.0


def test_order_result_mutable():
    result = OrderResult(
        order_id="123",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        status=OrderStatus.NEW,
        executed_qty=0.0,
        cummulative_quote_qty=0.0,
        price=0.0,
        order_type=OrderType.MARKET,
    )
    result.status = OrderStatus.FILLED
    assert result.status == OrderStatus.FILLED


def test_product_immutable():
    product = Product(
        product_id="prod_1",
        name="Widget",
        description=None,
        price_cents=100,
    )
    with pytest.raises(FrozenInstanceError):
        product.name = "Gadget"


def test_checkout_session_immutable():
    session = CheckoutSession(
        session_id="sess_1",
        url="https://checkout.stripe.com/sess_1",
        product_id="prod_1",
        status="open",
    )
    with pytest.raises(FrozenInstanceError):
        session.status = "complete"
