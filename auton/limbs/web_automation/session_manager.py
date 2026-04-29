"""Session manager for web automation.

Persists cookies, localStorage, and sessionStorage across tasks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages session persistence across browser contexts and pages.

    Parameters
    ----------
    storage_dir:
        Directory to save/load session state files.
    """

    def __init__(self, storage_dir: str | Path = "data/web_sessions") -> None:
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Storage state (cookies + localStorage)
    # ------------------------------------------------------------------ #

    async def save_session(
        self,
        context: BrowserContext,
        name: str,
    ) -> Path:
        """Save the full storage state of *context* to a JSON file.

        Returns the path written.
        """
        path = self._storage_dir / f"{name}.json"
        state = await context.storage_state()
        path.write_text(json.dumps(state, indent=2))
        logger.debug("SessionManager: saved session %s to %s", name, path)
        return path

    def load_session_state(self, name: str) -> dict[str, Any] | None:
        """Load a previously saved storage state dict (without creating a context)."""
        path = self._storage_dir / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    # ------------------------------------------------------------------ #
    # Granular localStorage / sessionStorage
    # ------------------------------------------------------------------ #

    async def get_local_storage(self, page: Page) -> dict[str, str]:
        """Return all localStorage key/value pairs for *page*."""
        return await page.evaluate("() => { const items = {}; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k) items[k] = localStorage.getItem(k); } return items; }")

    async def set_local_storage(self, page: Page, items: dict[str, str]) -> None:
        """Set localStorage key/value pairs on *page*."""
        for key, value in items.items():
            await page.evaluate(f"() => localStorage.setItem({json.dumps(key)}, {json.dumps(value)})")

    async def clear_local_storage(self, page: Page) -> None:
        await page.evaluate("() => localStorage.clear()")

    async def get_session_storage(self, page: Page) -> dict[str, str]:
        """Return all sessionStorage key/value pairs for *page*."""
        return await page.evaluate("() => { const items = {}; for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); if (k) items[k] = sessionStorage.getItem(k); } return items; }")

    async def set_session_storage(self, page: Page, items: dict[str, str]) -> None:
        """Set sessionStorage key/value pairs on *page*."""
        for key, value in items.items():
            await page.evaluate(f"() => sessionStorage.setItem({json.dumps(key)}, {json.dumps(value)})")

    async def clear_session_storage(self, page: Page) -> None:
        await page.evaluate("() => sessionStorage.clear()")

    # ------------------------------------------------------------------ #
    # Cookie helpers
    # ------------------------------------------------------------------ #

    async def get_cookies(self, context: BrowserContext) -> list[dict[str, Any]]:
        """Return cookies from *context*."""
        return await context.cookies()

    async def add_cookies(self, context: BrowserContext, cookies: list[dict[str, Any]]) -> None:
        """Add cookies to *context*."""
        await context.add_cookies(cookies)
        logger.debug("SessionManager: added %d cookies", len(cookies))

    async def clear_cookies(self, context: BrowserContext) -> None:
        await context.clear_cookies()

    # ------------------------------------------------------------------ #
    # Named session abstraction
    # ------------------------------------------------------------------ #

    async def persist_named_session(
        self,
        context: BrowserContext,
        page: Page,
        name: str,
    ) -> Path:
        """Persist full session: storage state + localStorage + sessionStorage."""
        path = self._storage_dir / f"{name}.json"
        state = await context.storage_state()

        local_storage = await self.get_local_storage(page)
        session_storage = await self.get_session_storage(page)

        payload = {
            "playwright_storage_state": state,
            "local_storage": local_storage,
            "session_storage": session_storage,
        }
        path.write_text(json.dumps(payload, indent=2))
        logger.debug("SessionManager: persisted named session %s to %s", name, path)
        return path

    def restore_named_session(self, name: str) -> dict[str, Any] | None:
        """Return a dict that can be passed to ``BrowserController.new_context(storage_state=...)``."""
        path = self._storage_dir / f"{name}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        # Return the playwright-compatible state; caller handles granular storage separately if needed
        return data.get("playwright_storage_state", data)

    def list_sessions(self) -> list[str]:
        """Return names of all saved sessions (without extension)."""
        return [p.stem for p in self._storage_dir.glob("*.json")]

    def delete_session(self, name: str) -> bool:
        path = self._storage_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False
