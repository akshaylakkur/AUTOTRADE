"""Navigator for web automation — navigate, click, scroll, wait."""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)


class Navigator:
    """High-level navigation primitives with retries and error recovery."""

    def __init__(self, page: Page) -> None:
        self._page = page

    # ------------------------------------------------------------------ #
    # Navigation
    # ------------------------------------------------------------------ #

    async def goto(
        self,
        url: str,
        *,
        wait_until: str = "networkidle",
        timeout_ms: int = 30000,
    ) -> None:
        """Navigate to *url* and wait for the specified load state."""
        logger.debug("Navigator: goto %s (wait_until=%s)", url, wait_until)
        await self._page.goto(url, wait_until=wait_until, timeout=timeout_ms)

    async def reload(self, *, wait_until: str = "networkidle", timeout_ms: int = 30000) -> None:
        """Reload the current page."""
        logger.debug("Navigator: reload")
        await self._page.reload(wait_until=wait_until, timeout=timeout_ms)

    async def go_back(self, *, wait_until: str = "networkidle", timeout_ms: int = 30000) -> None:
        """Go back in browser history."""
        await self._page.go_back(wait_until=wait_until, timeout=timeout_ms)

    # ------------------------------------------------------------------ #
    # Clicking
    # ------------------------------------------------------------------ #

    async def click(
        self,
        selector: str,
        *,
        timeout_ms: int = 10000,
        force: bool = False,
        delay_ms: int = 0,
    ) -> None:
        """Click an element matched by *selector*."""
        logger.debug("Navigator: click %s", selector)
        await self._page.click(
            selector,
            timeout=timeout_ms,
            force=force,
            delay=delay_ms,
        )

    async def click_if_visible(
        self,
        selector: str,
        *,
        timeout_ms: int = 5000,
    ) -> bool:
        """Click only if the element is visible; return whether clicked."""
        try:
            await self._page.wait_for_selector(selector, timeout=timeout_ms, state="visible")
            await self._page.click(selector)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def click_by_text(
        self,
        text: str,
        *,
        tag: str = "button",
        timeout_ms: int = 10000,
    ) -> None:
        """Click an element by its visible text content."""
        selector = f'{tag}:has-text("{text}")'
        logger.debug("Navigator: click_by_text %s", text)
        await self._page.click(selector, timeout=timeout_ms)

    # ------------------------------------------------------------------ #
    # Scrolling
    # ------------------------------------------------------------------ #

    async def scroll_to(
        self,
        selector: str,
        *,
        behavior: str = "smooth",
        timeout_ms: int = 10000,
    ) -> None:
        """Scroll until *selector* is in view."""
        element = await self._page.wait_for_selector(selector, timeout=timeout_ms)
        if element:
            await element.scroll_into_view_if_needed()
        else:
            raise RuntimeError(f"Element {selector!r} not found for scrolling")

    async def scroll_page(self, direction: str = "down", distance: int = 500) -> None:
        """Scroll the page by *distance* pixels."""
        sign = -1 if direction == "up" else 1
        await self._page.evaluate(f"window.scrollBy(0, {sign * distance})")

    async def scroll_to_bottom(self) -> None:
        """Scroll to the bottom of the page."""
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    # ------------------------------------------------------------------ #
    # Waiting
    # ------------------------------------------------------------------ #

    async def wait_for_selector(
        self,
        selector: str,
        *,
        state: str = "visible",
        timeout_ms: int = 30000,
    ) -> Any:
        """Wait for an element to reach *state*."""
        return await self._page.wait_for_selector(
            selector,
            state=state,  # type: ignore[arg-type]
            timeout=timeout_ms,
        )

    async def wait_for_navigation(
        self,
        *,
        wait_until: str = "networkidle",
        timeout_ms: int = 30000,
    ) -> None:
        """Wait for a page navigation to complete."""
        await self._page.wait_for_load_state(wait_until, timeout=timeout_ms)

    async def wait_for_url(
        self,
        url_pattern: str,
        *,
        timeout_ms: int = 30000,
    ) -> None:
        """Wait until the current URL matches *url_pattern*."""
        await self._page.wait_for_url(url_pattern, timeout=timeout_ms)

    async def wait_for_text(
        self,
        text: str,
        *,
        timeout_ms: int = 30000,
    ) -> None:
        """Wait until *text* appears somewhere on the page."""
        await self._page.wait_for_selector(
            f'text="{text}"',
            timeout=timeout_ms,
        )

    # ------------------------------------------------------------------ #
    # Extraction helpers
    # ------------------------------------------------------------------ #

    async def get_text(self, selector: str) -> str:
        """Return the inner text of an element."""
        element = await self._page.query_selector(selector)
        if element is None:
            raise RuntimeError(f"Element {selector!r} not found")
        return await element.inner_text()

    async def get_attribute(self, selector: str, attribute: str) -> str | None:
        """Return an attribute value of an element."""
        element = await self._page.query_selector(selector)
        if element is None:
            raise RuntimeError(f"Element {selector!r} not found")
        return await element.get_attribute(attribute)

    async def is_visible(self, selector: str) -> bool:
        """Return whether an element is currently visible."""
        element = await self._page.query_selector(selector)
        if element is None:
            return False
        return await element.is_visible()

    async def locator(self, selector: str) -> Locator:
        """Return a Playwright Locator for advanced chaining."""
        return self._page.locator(selector)

    # ------------------------------------------------------------------ #
    # Frame / iframe helpers
    # ------------------------------------------------------------------ #

    async def switch_to_frame(self, selector: str) -> Page:
        """Return a frame matched by *selector* as a Page-like object."""
        frame_element = await self._page.wait_for_selector(selector)
        if frame_element is None:
            raise RuntimeError(f"Frame {selector!r} not found")
        frame = await frame_element.content_frame()
        if frame is None:
            raise RuntimeError(f"Element {selector!r} is not a frame")
        return frame  # type: ignore[return-value]

    @property
    def page(self) -> Page:
        return self._page
