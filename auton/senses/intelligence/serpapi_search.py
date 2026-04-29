"""SerpAPI search provider wrapper."""

from __future__ import annotations

import logging

from auton.senses.intelligence.search_engine import SearchEngine
from auton.senses.intelligence.search_provider import SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class SerpAPISearchProvider(SearchProvider):
    """Search provider backed by SerpAPI (Google via SerpAPI).

    Wraps the existing ``SearchEngine`` and adapts it to the
    ``SearchProvider`` interface.
    """

    def __init__(self, api_key: str) -> None:
        self._engine = SearchEngine(serpapi_key=api_key, default_source="serpapi")

    async def connect(self) -> None:
        await self._engine.connect()

    async def disconnect(self) -> None:
        await self._engine.disconnect()

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Execute a search via SerpAPI and return canonical results."""
        raw_results = await self._engine.search(
            query, num_results=max_results, source="serpapi"
        )
        return [
            SearchResult(
                title=r.title,
                url=r.url,
                snippet=r.snippet,
                rank=r.rank,
                source=r.source,
                timestamp=r.timestamp,
            )
            for r in raw_results
        ]
