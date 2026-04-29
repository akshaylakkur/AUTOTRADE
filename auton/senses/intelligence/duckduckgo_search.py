"""DuckDuckGo HTML search provider — no API key required."""

from __future__ import annotations

import asyncio
import html as html_module
import logging
import re
from urllib.parse import quote_plus

import httpx

from auton.senses.intelligence.search_provider import SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class DuckDuckGoSearchProvider(SearchProvider):
    """Free web search via DuckDuckGo HTML endpoint.

    Parses the HTML response with regex to avoid heavy dependencies.
    Handles rate-limiting gracefully and returns empty results on any
    failure.
    """

    _URL = "https://html.duckduckgo.com/html/"

    def __init__(
        self,
        requests_per_minute: int = 10,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._semaphore = asyncio.Semaphore(requests_per_minute)
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client: httpx.AsyncClient | None = None
        self._last_request_time: float | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search DuckDuckGo and return parsed results."""
        if self._client is None:
            await self.connect()

        async with self._semaphore:
            await self._rate_limit()
            try:
                return await self._fetch(query, max_results)
            except Exception as exc:
                logger.warning("DuckDuckGo search failed: %s", exc)
                return []

    async def _fetch(self, query: str, max_results: int) -> list[SearchResult]:
        params = {"q": query}
        response = await self._client.get(self._URL, params=params)  # type: ignore[union-attr]
        response.raise_for_status()
        return self._parse(response.text, max_results)

    async def _rate_limit(self) -> None:
        now = asyncio.get_event_loop().time()
        if self._last_request_time is not None:
            elapsed = now - self._last_request_time
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(html_text: str, max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []

        # DuckDuckGo html results are wrapped in <div class="result ...">
        blocks = re.findall(
            r'<div class="result[^"]*">(.*?)</div>\s*</div>',
            html_text,
            re.IGNORECASE | re.DOTALL,
        )

        for idx, block in enumerate(blocks[:max_results], start=1):
            title_match = re.search(
                r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block,
                re.IGNORECASE | re.DOTALL,
            )
            if not title_match:
                continue

            url = html_module.unescape(title_match.group(1))
            title = re.sub(r"<[^>]+>", "", title_match.group(2))
            title = html_module.unescape(title).strip()

            snippet_match = re.search(
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                block,
                re.IGNORECASE | re.DOTALL,
            )
            snippet = ""
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1))
                snippet = html_module.unescape(snippet).strip()

            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    rank=idx,
                    source="duckduckgo",
                )
            )

        return results
