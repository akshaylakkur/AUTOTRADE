"""Abstract search provider interface for ÆON intelligence."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str
    rank: int = 0
    source: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SearchProvider(ABC):
    """Abstract base class for web search providers."""

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Execute a web search and return results.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.

        Returns:
            A list of ``SearchResult`` ordered by relevance.
        """
        ...

    async def connect(self) -> None:
        """Prepare any network connections. Optional — no-op by default."""

    async def disconnect(self) -> None:
        """Release network connections. Optional — no-op by default."""


def create_search_provider() -> SearchProvider | None:
    """Factory that returns the best available search provider.

    Prefers SerpAPI when ``SERPAPI_KEY`` is configured, otherwise falls
    back to DuckDuckGo (no API key required).
    """
    from auton.core.config import AeonConfig

    serpapi_key = AeonConfig.SERPAPI_KEY or os.getenv("SERPAPI_KEY", "")
    if serpapi_key:
        from auton.senses.intelligence.serpapi_search import SerpAPISearchProvider

        logger.info("Using SerpAPI search provider.")
        return SerpAPISearchProvider(api_key=serpapi_key)

    from auton.senses.intelligence.duckduckgo_search import DuckDuckGoSearchProvider

    logger.info("Using DuckDuckGo search provider (no API key).")
    return DuckDuckGoSearchProvider()
