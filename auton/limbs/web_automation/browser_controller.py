"""Browser controller for Project ÆON web automation.

Manages Playwright browser lifecycle, contexts, pages, and sessions.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

logger = logging.getLogger(__name__)


class BrowserController:
    """Async Playwright browser controller with resilience patterns.

    Parameters
    ----------
    headless:
        Run browser in headless mode (default ``True``).
    browser_type:
        One of ``chromium``, ``firefox``, ``webkit`` (default ``chromium``).
    user_data_dir:
        Optional persistent profile directory.
    launch_args:
        Extra arguments passed to the browser executable.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        browser_type: str = "chromium",
        user_data_dir: str | None = None,
        launch_args: list[str] | None = None,
    ) -> None:
        self._headless = headless
        self._browser_type_name = browser_type
        self._user_data_dir = user_data_dir
        self._launch_args = list(launch_args) if launch_args else []
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._pages: dict[str, Page] = {}
        self._context_counter = 0
        self._page_counter = 0

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> "BrowserController":
        """Launch the browser (idempotent)."""
        if self._browser is not None:
            return self

        self._playwright = await async_playwright().start()
        browser_cls: BrowserType = getattr(
            self._playwright, self._browser_type_name
        )

        launch_kwargs: dict[str, Any] = {
            "headless": self._headless,
            "args": self._launch_args,
        }
        if self._user_data_dir:
            launch_kwargs["user_data_dir"] = self._user_data_dir

        self._browser = await browser_cls.launch(**launch_kwargs)
        logger.info("BrowserController: %s started (headless=%s)", self._browser_type_name, self._headless)
        return self

    async def stop(self) -> None:
        """Close all pages, contexts, and the browser."""
        for page in list(self._pages.values()):
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
        self._pages.clear()

        for ctx in list(self._contexts.values()):
            try:
                await ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self._contexts.clear()

        if self._browser:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None

        logger.info("BrowserController: stopped")

    async def __aenter__(self) -> "BrowserController":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------ #
    # Context management
    # ------------------------------------------------------------------ #

    async def new_context(
        self,
        *,
        context_id: str | None = None,
        viewport: dict[str, int] | None = None,
        locale: str = "en-US",
        geolocation: dict[str, float] | None = None,
        permissions: list[str] | None = None,
        extra_http_headers: dict[str, str] | None = None,
        storage_state: dict[str, Any] | str | None = None,
    ) -> str:
        """Create a new browser context and return its ID.

        Parameters
        ----------
        context_id:
            Optional explicit ID; otherwise auto-generated.
        viewport:
            ``{"width": int, "height": int}``.
        locale:
            Browser locale.
        geolocation:
            ``{"latitude": float, "longitude": float}``.
        permissions:
            List of permissions to grant, e.g. ``["geolocation"]``.
        extra_http_headers:
            Headers added to every request.
        storage_state:
            Playwright storage state dict or path to load.
        """
        if self._browser is None:
            raise RuntimeError("Browser not started; call start() first")

        ctx_kwargs: dict[str, Any] = {"locale": locale}
        if viewport:
            ctx_kwargs["viewport"] = viewport
        if geolocation:
            ctx_kwargs["geolocation"] = geolocation
        if permissions:
            ctx_kwargs["permissions"] = permissions
        if extra_http_headers:
            ctx_kwargs["extra_http_headers"] = extra_http_headers
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state

        context = await self._browser.new_context(**ctx_kwargs)
        self._context_counter += 1
        cid = context_id or f"ctx_{self._context_counter}"
        self._contexts[cid] = context
        logger.debug("BrowserController: new context %s", cid)
        return cid

    async def close_context(self, context_id: str) -> None:
        """Close a context and all its pages."""
        context = self._contexts.pop(context_id, None)
        if context is None:
            return
        # close tracked pages belonging to this context
        pages_to_remove = [
            pid for pid, page in self._pages.items() if page.context == context
        ]
        for pid in pages_to_remove:
            self._pages.pop(pid, None)
        await context.close()
        logger.debug("BrowserController: closed context %s", context_id)

    async def get_context(self, context_id: str) -> BrowserContext:
        if context_id not in self._contexts:
            raise KeyError(f"Context {context_id!r} not found")
        return self._contexts[context_id]

    # ------------------------------------------------------------------ #
    # Page management
    # ------------------------------------------------------------------ #

    async def new_page(
        self,
        context_id: str | None = None,
        *,
        page_id: str | None = None,
    ) -> tuple[str, Page]:
        """Open a new page in *context_id* (or the default context).

        Returns ``(page_id, page)``.
        """
        if self._browser is None:
            raise RuntimeError("Browser not started; call start() first")

        if context_id is None:
            if not self._contexts:
                context_id = await self.new_context()
            else:
                context_id = next(iter(self._contexts))

        context = await self.get_context(context_id)
        page = await context.new_page()
        self._page_counter += 1
        pid = page_id or f"page_{self._page_counter}"
        self._pages[pid] = page
        logger.debug("BrowserController: new page %s in context %s", pid, context_id)
        return pid, page

    async def close_page(self, page_id: str) -> None:
        page = self._pages.pop(page_id, None)
        if page:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
        logger.debug("BrowserController: closed page %s", page_id)

    def get_page(self, page_id: str) -> Page:
        if page_id not in self._pages:
            raise KeyError(f"Page {page_id!r} not found")
        return self._pages[page_id]

    def list_pages(self) -> dict[str, Page]:
        return dict(self._pages)

    def list_contexts(self) -> dict[str, BrowserContext]:
        return dict(self._contexts)

    # ------------------------------------------------------------------ #
    # Storage helpers
    # ------------------------------------------------------------------ #

    async def save_storage_state(
        self,
        context_id: str,
        path: str | Path,
    ) -> None:
        """Save cookies + localStorage to a JSON file."""
        context = await self.get_context(context_id)
        await context.storage_state(path=str(path))
        logger.debug("BrowserController: saved storage state for %s to %s", context_id, path)

    async def load_storage_state(
        self,
        context_id: str,
        path: str | Path,
    ) -> None:
        """Load cookies + localStorage from a JSON file into an existing context.

        Note: Playwright requires a new context to load storage state,
        so this closes the old context and creates a replacement with
        the same ID.
        """
        old_ctx = self._contexts.pop(context_id, None)
        if old_ctx:
            for pid, page in list(self._pages.items()):
                if page.context == old_ctx:
                    self._pages.pop(pid, None)
                    try:
                        await page.close()
                    except Exception:  # noqa: BLE001
                        pass
            try:
                await old_ctx.close()
            except Exception:  # noqa: BLE001
                pass

        if self._browser is None:
            raise RuntimeError("Browser not started")

        new_ctx = await self._browser.new_context(
            storage_state=str(path),
        )
        self._contexts[context_id] = new_ctx
        logger.debug("BrowserController: loaded storage state into %s", context_id)

    # ------------------------------------------------------------------ #
    # Resilience helpers
    # ------------------------------------------------------------------ #

    async def with_retry(
        self,
        coro_factory,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> Any:
        """Execute *coro_factory* with exponential backoff on failure.

        *coro_factory* must be an async callable (e.g. ``lambda: page.click(...)``)
        so a fresh coroutine is created on each attempt.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await coro_factory()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == max_retries:
                    break
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "BrowserController: attempt %d failed (%s), retrying in %.1fs",
                    attempt + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    @property
    def is_running(self) -> bool:
        return self._browser is not None and self._playwright is not None
