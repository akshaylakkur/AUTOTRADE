"""Web search via APIs with result ranking and caching."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    """A single ranked search result."""

    title: str
    url: str
    snippet: str
    rank: int
    source: str  # e.g., "serpapi", "brave"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SearchEngine:
    """Unified search interface supporting SerpAPI and Brave Search.

    Uses an in-memory LRU cache to minimize API spend and an async
    semaphore for rate-limiting.
    """

    _SERPAPI_URL = "https://serpapi.com/search"
    _BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    @classmethod
    def is_configured(cls, provider: str = "serpapi") -> bool:
        """Return True when the given search provider has a configured API key.

        Args:
            provider: ``"serpapi"`` or ``"brave"``.
        """
        if provider == "serpapi":
            return bool(os.getenv("SERPAPI_KEY", "").strip())
        if provider == "brave":
            return bool(os.getenv("BRAVE_API_KEY", "").strip())
        return False

    def __init__(
        self,
        serpapi_key: str | None = None,
        brave_api_key: str | None = None,
        cache_size: int = 256,
        requests_per_minute: int = 10,
        default_source: str = "brave",
    ) -> None:
        self._serpapi_key = serpapi_key or os.getenv("SERPAPI_KEY", "")
        self._brave_api_key = brave_api_key or os.getenv("BRAVE_API_KEY", "")
        self._default_source = default_source if self._supports(default_source) else "serpapi"
        self._cache: dict[str, tuple[list[SearchResult], datetime]] = {}
        self._cache_size = cache_size
        self._semaphore = asyncio.Semaphore(requests_per_minute)
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        num_results: int = 10,
        source: str | None = None,
    ) -> list[SearchResult]:
        """Execute a web search and return ranked results.

        Args:
            query: The search query string.
            num_results: Maximum number of results to return.
            source: Override the default search provider (``serpapi`` or ``brave``).

        Returns:
            A list of ``SearchResult`` ordered by rank.
        """
        provider = source or self._default_source
        if not self._supports(provider):
            raise RuntimeError(f"Search provider '{provider}' is not configured (missing API key).")

        cache_key = self._cache_key(provider, query, num_results)
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("Search cache hit for query: %s", query)
            return cached

        if self._client is None:
            await self.connect()

        async with self._semaphore:
            if provider == "serpapi":
                results = await self._search_serpapi(query, num_results)
            else:
                results = await self._search_brave(query, num_results)

        self._cache_put(cache_key, results)
        return results

    async def search_multiple(
        self,
        queries: list[str],
        num_results: int = 10,
        source: str | None = None,
    ) -> dict[str, list[SearchResult]]:
        """Run multiple searches concurrently."""
        tasks = {q: asyncio.create_task(self.search(q, num_results, source)) for q in queries}
        return {q: await task for q, task in tasks.items()}

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    async def _search_serpapi(self, query: str, num_results: int) -> list[SearchResult]:
        if not self._serpapi_key:
            raise RuntimeError("SerpAPI key is not configured.")

        params = {
            "q": query,
            "api_key": self._serpapi_key,
            "engine": "google",
            "num": min(num_results, 100),
        }
        response = await self._client.get(self._SERPAPI_URL, params=params)  # type: ignore[union-attr]
        response.raise_for_status()
        data = response.json()

        organic = data.get("organic_results", [])
        results: list[SearchResult] = []
        for idx, item in enumerate(organic[:num_results], start=1):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    rank=idx,
                    source="serpapi",
                )
            )
        return results

    async def _search_brave(self, query: str, num_results: int) -> list[SearchResult]:
        if not self._brave_api_key:
            raise RuntimeError("Brave API key is not configured.")

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._brave_api_key,
        }
        params = {
            "q": query,
            "count": min(num_results, 20),
            "offset": 0,
        }
        response = await self._client.get(self._BRAVE_URL, headers=headers, params=params)  # type: ignore[union-attr]
        response.raise_for_status()
        data = response.json()

        web_results = data.get("web", {}).get("results", [])
        results: list[SearchResult] = []
        for idx, item in enumerate(web_results[:num_results], start=1):
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    rank=idx,
                    source="brave",
                )
            )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _supports(self, provider: str) -> bool:
        if provider == "serpapi":
            return bool(self._serpapi_key)
        if provider == "brave":
            return bool(self._brave_api_key)
        return False

    def _cache_key(self, provider: str, query: str, num_results: int) -> str:
        payload = f"{provider}:{query}:{num_results}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def _cache_get(self, key: str) -> list[SearchResult] | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        results, ts = entry
        if datetime.now(timezone.utc) - ts > timedelta(minutes=30):
            del self._cache[key]
            return None
        return results

    def _cache_put(self, key: str, results: list[SearchResult]) -> None:
        if len(self._cache) >= self._cache_size:
            # Evict oldest
            oldest = min(self._cache, key=lambda k: self._cache[k][1])
            del self._cache[oldest]
        self._cache[key] = (results, datetime.now(timezone.utc))
