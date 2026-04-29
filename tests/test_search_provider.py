"""Tests for the search provider abstraction and DuckDuckGo/SerpAPI implementations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auton.senses.intelligence.duckduckgo_search import DuckDuckGoSearchProvider
from auton.senses.intelligence.search_provider import (
    SearchProvider,
    SearchResult,
    create_search_provider,
)
from auton.senses.intelligence.serpapi_search import SerpAPISearchProvider


# ---------------------------------------------------------------------------
# SearchResult
# ---------------------------------------------------------------------------


def test_search_result_defaults() -> None:
    r = SearchResult(title="T", url="https://a.com", snippet="S")
    assert r.title == "T"
    assert r.url == "https://a.com"
    assert r.snippet == "S"
    assert r.rank == 0
    assert r.source == ""
    assert isinstance(r.timestamp, datetime)


def test_search_result_full() -> None:
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    r = SearchResult(
        title="T", url="https://a.com", snippet="S", rank=1, source="x", timestamp=ts
    )
    assert r.rank == 1
    assert r.source == "x"
    assert r.timestamp == ts


# ---------------------------------------------------------------------------
# DuckDuckGoSearchProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duckduckgo_connect_disconnect() -> None:
    provider = DuckDuckGoSearchProvider()
    assert provider._client is None
    await provider.connect()
    assert provider._client is not None
    await provider.disconnect()
    assert provider._client is None


@pytest.mark.asyncio
async def test_duckduckgo_parses_html() -> None:
    html = """
    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a class="result__a" href="https://example.com/1">Result One</a>
      </h2>
      <a class="result__snippet">This is the first snippet.</a>
    </div>
    </div>
    <div class="result results_links results_links_deep web-result">
      <h2 class="result__title">
        <a class="result__a" href="https://example.com/2">Result Two</a>
      </h2>
      <a class="result__snippet">Second snippet here.</a>
    </div>
    </div>
    """
    provider = DuckDuckGoSearchProvider()
    await provider.connect()
    provider._client.get = AsyncMock(  # type: ignore[method-assign,union-attr]
        return_value=_mock_response(200, text=html)
    )

    results = await provider.search("test query", max_results=2)
    assert len(results) == 2
    assert results[0].title == "Result One"
    assert results[0].url == "https://example.com/1"
    assert results[0].snippet == "This is the first snippet."
    assert results[0].rank == 1
    assert results[0].source == "duckduckgo"
    assert results[1].title == "Result Two"
    assert results[1].rank == 2

    await provider.disconnect()


@pytest.mark.asyncio
async def test_duckduckgo_returns_empty_on_http_error() -> None:
    provider = DuckDuckGoSearchProvider()
    await provider.connect()
    provider._client.get = AsyncMock(  # type: ignore[method-assign,union-attr]
        side_effect=Exception("Connection refused")
    )

    results = await provider.search("test query")
    assert results == []
    await provider.disconnect()


@pytest.mark.asyncio
async def test_duckduckgo_returns_empty_on_no_results() -> None:
    provider = DuckDuckGoSearchProvider()
    await provider.connect()
    provider._client.get = AsyncMock(  # type: ignore[method-assign,union-attr]
        return_value=_mock_response(200, text="<html><body>No results</body></html>")
    )

    results = await provider.search("test query")
    assert results == []
    await provider.disconnect()


@pytest.mark.asyncio
async def test_duckduckgo_rate_limit() -> None:
    provider = DuckDuckGoSearchProvider()
    await provider.connect()
    provider._client.get = AsyncMock(  # type: ignore[method-assign,union-attr]
        return_value=_mock_response(200, text="<html><body></body></html>")
    )

    await provider.search("q1")
    await provider.search("q2")
    # Two calls should have been made despite rate limiting
    assert provider._client.get.call_count == 2  # type: ignore[union-attr]
    await provider.disconnect()


# ---------------------------------------------------------------------------
# SerpAPISearchProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serpapi_provider_delegates_to_engine() -> None:
    provider = SerpAPISearchProvider(api_key="test_key")
    await provider.connect()

    raw = [
        SearchResult(title="A", url="https://a.com", snippet="S", rank=1, source="serpapi"),
    ]
    provider._engine.search = AsyncMock(return_value=raw)  # type: ignore[method-assign]

    results = await provider.search("query", max_results=3)
    assert len(results) == 1
    assert results[0].title == "A"
    assert results[0].source == "serpapi"
    provider._engine.search.assert_awaited_once_with("query", num_results=3, source="serpapi")

    await provider.disconnect()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_returns_serpapi_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERPAPI_KEY", "secret")
    with patch("auton.senses.intelligence.serpapi_search.SerpAPISearchProvider") as mock_cls:
        mock_cls.return_value = MagicMock(spec=SearchProvider)
        provider = create_search_provider()
        assert provider is not None
        mock_cls.assert_called_once_with(api_key="secret")


def test_factory_returns_duckduckgo_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    with patch("auton.core.config.AeonConfig.SERPAPI_KEY", ""):
        provider = create_search_provider()
        assert isinstance(provider, DuckDuckGoSearchProvider)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status: int, json: Any = None, text: str = "") -> Any:
    import httpx

    request = httpx.Request("GET", "http://test")
    if json is not None:
        return httpx.Response(status, json=json, request=request)
    return httpx.Response(status, text=text, request=request)
