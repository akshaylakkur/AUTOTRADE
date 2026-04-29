"""Content extraction from web pages."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor using stdlib."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._skip_tags = {"script", "style", "nav", "footer", "header", "aside", "noscript"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self._chunks.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._chunks)


@dataclass(frozen=True)
class ScrapedContent:
    """Extracted content from a web page."""

    url: str
    title: str
    text: str
    word_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class WebScraper:
    """Async web scraper with rate limiting and basic text extraction.

    Uses stdlib ``HTMLParser`` to strip tags and boilerplate. For production
    workloads, swap in ``trafilatura`` or ``readability-lxml``.
    """

    def __init__(
        self,
        requests_per_minute: int = 30,
        max_content_length: int = 500_000,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._semaphore = asyncio.Semaphore(requests_per_minute)
        self._max_content_length = max_content_length
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Scraping
    # ------------------------------------------------------------------

    async def scrape(self, url: str) -> ScrapedContent | None:
        """Fetch and extract clean text from a URL.

        Returns ``None`` if the fetch fails or the content is unreadable.
        """
        if self._client is None:
            await self.connect()

        async with self._semaphore:
            try:
                response = await self._client.get(url)  # type: ignore[union-attr]
                response.raise_for_status()
                raw = response.text[: self._max_content_length]
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", url, exc)
                return None

        title = self._extract_title(raw)
        text = self._extract_text(raw)
        if not text:
            return None

        return ScrapedContent(
            url=url,
            title=title,
            text=text,
            word_count=len(text.split()),
        )

    async def scrape_multiple(self, urls: list[str], concurrency: int = 5) -> dict[str, ScrapedContent | None]:
        """Scrape multiple URLs concurrently with bounded parallelism."""
        semaphore = asyncio.Semaphore(concurrency)

        async def _bound_scrape(u: str) -> tuple[str, ScrapedContent | None]:
            async with semaphore:
                return u, await self.scrape(u)

        tasks = [asyncio.create_task(_bound_scrape(u)) for u in urls]
        results = await asyncio.gather(*tasks)
        return {url: content for url, content in results}

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(html_text: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
        if match:
            return html.unescape(re.sub(r"<[^>]+>", "", match.group(1)).strip())
        return ""

    @staticmethod
    def _extract_text(html_text: str) -> str:
        extractor = _TextExtractor()
        try:
            extractor.feed(html_text)
        except Exception:
            # Malformed HTML fallback
            pass
        text = extractor.get_text()
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()
