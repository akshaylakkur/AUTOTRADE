"""Stripe commerce skeleton — returns mock data when no API key is configured."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from auton.limbs.base_limb import BaseLimb
from auton.limbs.dataclasses import CheckoutSession, Product


class StripeLimb(BaseLimb):
    """Async limb for Stripe product and checkout management.

    Parameters
    ----------
    api_key:
        Stripe secret key.  When ``None`` the limb operates in skeleton
        mode and returns mock objects.
    base_url:
        Stripe API base URL.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.stripe.com",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._mock_products: dict[str, dict[str, Any]] = {}
        self._mock_sessions: dict[str, dict[str, Any]] = {}
        self._mock_counter = 0

        auth = (self._api_key, "") if self._api_key else None
        self._client = httpx.AsyncClient(base_url=self._base_url, auth=auth, timeout=30.0)

    # ------------------------------------------------------------------ #
    # BaseLimb interface
    # ------------------------------------------------------------------ #

    async def execute(self, action: Any) -> Any:
        method = action.get("method")
        kwargs = action.get("kwargs", {})
        if method == "create_product":
            return await self.create_product(**kwargs)
        if method == "create_checkout_session":
            return await self.create_checkout_session(**kwargs)
        if method == "list_products":
            return await self.list_products()
        raise ValueError(f"Unknown action: {method}")

    async def get_cost_estimate(self, action: Any) -> float:
        """Stripe API calls are free; payment processing fees are post-hoc."""
        return 0.0

    def is_available(self, tier: int) -> bool:
        return tier >= 1

    async def health_check(self) -> dict[str, Any]:
        if self._api_key is None:
            return {"status": "skeleton", "mode": "mock"}
        try:
            start = asyncio.get_event_loop().time()
            r = await self._client.get("/v1/account")
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
    # Stripe API wrappers
    # ------------------------------------------------------------------ #

    async def create_product(
        self,
        name: str,
        description: str | None = None,
        price_cents: int = 0,
    ) -> Product:
        self._emit("limb.product.create", {"name": name, "price_cents": price_cents})

        if self._api_key is None:
            product = self._mock_create_product(name, description, price_cents)
        else:
            product = await self._live_create_product(name, description, price_cents)

        await self._charge(0.0, f"stripe_product_created:{product.product_id}")
        self._emit("limb.product.created", {"product_id": product.product_id})
        return product

    async def create_checkout_session(
        self,
        product_id: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        self._emit("limb.checkout.create", {"product_id": product_id})

        if self._api_key is None:
            session = self._mock_create_checkout_session(product_id, success_url, cancel_url)
        else:
            session = await self._live_create_checkout_session(product_id, success_url, cancel_url)

        await self._charge(0.0, f"stripe_checkout_created:{session.session_id}")
        self._emit("limb.checkout.created", {"session_id": session.session_id})
        return session

    async def list_products(self) -> list[Product]:
        if self._api_key is None:
            return [
                Product(
                    product_id=p["id"],
                    name=p["name"],
                    description=p.get("description"),
                    price_cents=p.get("price_cents", 0),
                    raw_response=p,
                )
                for p in self._mock_products.values()
            ]
        data = await self._live_list_products()
        return [
            Product(
                product_id=item["id"],
                name=item["name"],
                description=item.get("description"),
                price_cents=_extract_price_cents(item),
                raw_response=item,
            )
            for item in data
        ]

    # ------------------------------------------------------------------ #
    # Mock implementations
    # ------------------------------------------------------------------ #

    def _mock_create_product(
        self,
        name: str,
        description: str | None,
        price_cents: int,
    ) -> Product:
        self._mock_counter += 1
        prod_id = f"mock_prod_{self._mock_counter}"
        self._mock_products[prod_id] = {
            "id": prod_id,
            "name": name,
            "description": description,
            "price_cents": price_cents,
            "active": True,
        }
        return Product(
            product_id=prod_id,
            name=name,
            description=description,
            price_cents=price_cents,
            raw_response=self._mock_products[prod_id],
        )

    def _mock_create_checkout_session(
        self,
        product_id: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        self._mock_counter += 1
        session_id = f"mock_sess_{self._mock_counter}"
        self._mock_sessions[session_id] = {
            "id": session_id,
            "product_id": product_id,
            "url": f"https://checkout.stripe.com/mock/{session_id}",
            "status": "open",
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        return CheckoutSession(
            session_id=session_id,
            url=self._mock_sessions[session_id]["url"],
            product_id=product_id,
            status="open",
            raw_response=self._mock_sessions[session_id],
        )

    # ------------------------------------------------------------------ #
    # Live implementations
    # ------------------------------------------------------------------ #

    async def _live_create_product(
        self,
        name: str,
        description: str | None,
        price_cents: int,
    ) -> Product:
        payload = {"name": name}
        if description:
            payload["description"] = description

        r = await self._client.post("/v1/products", data=payload)
        r.raise_for_status()
        prod = r.json()

        # attach price
        price_payload = {
            "product": prod["id"],
            "unit_amount": price_cents,
            "currency": "usd",
        }
        pr = await self._client.post("/v1/prices", data=price_payload)
        pr.raise_for_status()
        price_data = pr.json()

        return Product(
            product_id=prod["id"],
            name=prod["name"],
            description=prod.get("description"),
            price_cents=price_data.get("unit_amount", price_cents),
            raw_response={"product": prod, "price": price_data},
        )

    async def _live_create_checkout_session(
        self,
        product_id: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        # Retrieve the default price for the product
        r = await self._client.get("/v1/prices", params={"product": product_id, "limit": 1})
        r.raise_for_status()
        prices = r.json().get("data", [])
        if not prices:
            raise ValueError(f"No price found for product {product_id}")

        payload = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price]": prices[0]["id"],
            "line_items[0][quantity]": 1,
        }
        r = await self._client.post("/v1/checkout/sessions", data=payload)
        r.raise_for_status()
        sess = r.json()
        return CheckoutSession(
            session_id=sess["id"],
            url=sess["url"],
            product_id=product_id,
            status=sess.get("status", "open"),
            raw_response=sess,
        )

    async def _live_list_products(self) -> list[dict[str, Any]]:
        r = await self._client.get("/v1/products")
        r.raise_for_status()
        return r.json().get("data", [])

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> StripeLimb:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


def _extract_price_cents(stripe_product: dict[str, Any]) -> int:
    # Best-effort extraction from embedded price data if present.
    default_price = stripe_product.get("default_price")
    if isinstance(default_price, dict):
        return default_price.get("unit_amount", 0)
    return 0
