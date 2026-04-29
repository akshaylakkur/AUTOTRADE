"""CommerceSimulator — simulated SaaS/e-commerce without real deployment."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from auton.limbs.commerce.stripe_limb import StripeLimb
from auton.limbs.dataclasses import CheckoutSession, Product
from auton.simulation.clock import SimulationClock
from auton.simulation.connectors.dataclasses import SimulatedData
from auton.simulation.recorder import SimulationRecorder
from auton.simulation.wallet import SimulatedWallet


class CommerceSimulator:
    """Simulates SaaS and e-commerce actions without touching real APIs.

    Wraps a :class:`StripeLimb` (skeleton mode by default) and routes
    all platform fees and simulated revenue through a
    :class:`SimulatedWallet`. Every action is tagged as simulated and
    recorded via :class:`SimulationRecorder`.
    """

    # Stripe-like fee schedule: 2.9% + 30c per transaction
    _STRIPE_FEE_RATE = 0.029
    _STRIPE_FEE_FIXED = 0.30

    def __init__(
        self,
        stripe_limb: StripeLimb | None = None,
        wallet: SimulatedWallet | None = None,
        recorder: SimulationRecorder | None = None,
        clock: SimulationClock | None = None,
    ) -> None:
        self._stripe_limb = stripe_limb or StripeLimb(api_key=None)
        self._wallet = wallet
        self._recorder = recorder
        self._clock = clock

    # ------------------------------------------------------------------
    # Product lifecycle
    # ------------------------------------------------------------------

    async def create_product(
        self,
        name: str,
        description: str | None = None,
        price_cents: int = 0,
    ) -> SimulatedData:
        """Create a mock product and debit the wallet for platform fees."""
        product: Product = await self._stripe_limb.create_product(
            name=name, description=description, price_cents=price_cents
        )

        # Simulate a one-time platform fee (e.g. Stripe Connect)
        platform_fee = self._STRIPE_FEE_FIXED
        if self._wallet is not None:
            self._wallet.debit(platform_fee, f"sim_platform_fee:{product.product_id}")

        sim = SimulatedData(
            source="commerce",
            data_type="product",
            payload=product,
            sim_time=self._sim_time(),
            metadata={
                "action": "create_product",
                "platform_fee": platform_fee,
                "simulated": True,
            },
        )
        self._record("commerce", "product_created", {"product_id": product.product_id, "fee": platform_fee})
        return sim

    async def create_checkout_session(
        self,
        product_id: str,
        success_url: str,
        cancel_url: str,
    ) -> SimulatedData:
        """Create a mock checkout session."""
        session: CheckoutSession = await self._stripe_limb.create_checkout_session(
            product_id=product_id,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        sim = SimulatedData(
            source="commerce",
            data_type="checkout_session",
            payload=session,
            sim_time=self._sim_time(),
            metadata={
                "action": "create_checkout",
                "product_id": product_id,
                "simulated": True,
            },
        )
        self._record("commerce", "checkout_created", {"session_id": session.session_id})
        return sim

    async def simulate_purchase(
        self,
        session_id: str,
        product_price_cents: int = 0,
    ) -> SimulatedData:
        """Simulate a customer completing a purchase.

        Credits the wallet with net revenue after deducting processing fees.
        """
        revenue = product_price_cents / 100.0
        if revenue <= 0.0:
            fee = 0.0
            net = 0.0
        else:
            fee = revenue * self._STRIPE_FEE_RATE + self._STRIPE_FEE_FIXED
            net = max(0.0, revenue - fee)

        if self._wallet is not None and net > 0:
            self._wallet.credit(net, f"sim_revenue:{session_id}")

        sim = SimulatedData(
            source="commerce",
            data_type="purchase",
            payload={
                "session_id": session_id,
                "revenue": revenue,
                "fee": fee,
                "net": net,
            },
            sim_time=self._sim_time(),
            metadata={"action": "simulate_purchase", "simulated": True},
        )
        self._record("commerce", "purchase_simulated", {"session_id": session_id, "net": net})
        return sim

    async def list_products(self) -> SimulatedData:
        """List all mock products."""
        products: list[Product] = await self._stripe_limb.list_products()
        sim = SimulatedData(
            source="commerce",
            data_type="product_list",
            payload=products,
            sim_time=self._sim_time(),
            metadata={"action": "list_products", "count": len(products), "simulated": True},
        )
        self._record("commerce", "products_listed", {"count": len(products)})
        return sim

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying Stripe limb."""
        await self._stripe_limb.close()

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
