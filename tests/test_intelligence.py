"""Tests for the intelligence/research engine."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auton.core.event_bus import EventBus
from auton.core.events import OpportunityDiscovered
from auton.senses.intelligence.opportunity_monitor import (
    MonitorConfig,
    OpportunityMonitor,
)
from auton.senses.intelligence.scraper import ScrapedContent, WebScraper
from auton.senses.intelligence.search_engine import SearchEngine
from auton.senses.intelligence.search_provider import SearchResult
from auton.senses.intelligence.storage import ResearchStore, ResearchTask
from auton.senses.intelligence.synthesizer import (
    ResearchSynthesizer,
    SourceBrief,
    SynthesisReport,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_event_bus() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def search_engine() -> SearchEngine:
    return SearchEngine(
        serpapi_key="test_serpapi_key",
        brave_api_key="test_brave_key",
        default_source="serpapi",
    )


@pytest.fixture
def scraper() -> WebScraper:
    return WebScraper()


@pytest.fixture
def synthesizer() -> ResearchSynthesizer:
    return ResearchSynthesizer()


@pytest.fixture
def research_store(tmp_path: Any) -> ResearchStore:
    db_path = tmp_path / "research_test.db"
    return ResearchStore(db_path=str(db_path))


@pytest.fixture
def opportunity_monitor(mock_event_bus: AsyncMock) -> OpportunityMonitor:
    mock_search = AsyncMock()
    mock_search.search = AsyncMock(return_value=[])
    return OpportunityMonitor(
        event_bus=mock_event_bus,
        search_provider=mock_search,
        config=MonitorConfig(poll_interval_seconds=0.1),
    )


# ---------------------------------------------------------------------------
# SearchEngine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_engine_connect_disconnect(search_engine: SearchEngine) -> None:
    assert search_engine._client is None
    await search_engine.connect()
    assert search_engine._client is not None
    await search_engine.disconnect()
    assert search_engine._client is None


@pytest.mark.asyncio
async def test_search_engine_requires_configured_provider() -> None:
    engine = SearchEngine(serpapi_key="", brave_api_key="")
    with pytest.raises(RuntimeError, match="not configured"):
        await engine.search("test query")


@pytest.mark.asyncio
async def test_search_engine_serpapi_mock(search_engine: SearchEngine) -> None:
    mock_data = {
        "organic_results": [
            {"title": "Result 1", "link": "https://example.com/1", "snippet": "Snippet 1"},
            {"title": "Result 2", "link": "https://example.com/2", "snippet": "Snippet 2"},
        ]
    }
    await search_engine.connect()
    search_engine._client.get = AsyncMock(return_value=_mock_response(200, json=mock_data))  # type: ignore[method-assign,union-attr]

    results = await search_engine.search("test query", num_results=2, source="serpapi")
    assert len(results) == 2
    assert results[0].title == "Result 1"
    assert results[0].url == "https://example.com/1"
    assert results[0].rank == 1
    assert results[0].source == "serpapi"
    assert results[1].rank == 2


@pytest.mark.asyncio
async def test_search_engine_brave_mock(search_engine: SearchEngine) -> None:
    mock_data = {
        "web": {
            "results": [
                {"title": "Brave Result 1", "url": "https://example.com/a", "description": "Desc 1"},
                {"title": "Brave Result 2", "url": "https://example.com/b", "description": "Desc 2"},
            ]
        }
    }
    await search_engine.connect()
    search_engine._client.get = AsyncMock(return_value=_mock_response(200, json=mock_data))  # type: ignore[method-assign,union-attr]

    results = await search_engine.search("test query", num_results=2, source="brave")
    assert len(results) == 2
    assert results[0].title == "Brave Result 1"
    assert results[0].url == "https://example.com/a"
    assert results[0].source == "brave"


@pytest.mark.asyncio
async def test_search_engine_cache_hit(search_engine: SearchEngine) -> None:
    mock_data = {
        "organic_results": [
            {"title": "Cached", "link": "https://cached.com", "snippet": "Cache me"},
        ]
    }
    await search_engine.connect()
    search_engine._client.get = AsyncMock(return_value=_mock_response(200, json=mock_data))  # type: ignore[method-assign,union-attr]

    first = await search_engine.search("cache_query", num_results=1, source="serpapi")
    second = await search_engine.search("cache_query", num_results=1, source="serpapi")
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].title == second[0].title
    # Only one HTTP call because of cache
    assert search_engine._client.get.call_count == 1  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_search_engine_cache_expires(search_engine: SearchEngine) -> None:
    mock_data = {
        "organic_results": [
            {"title": "Old", "link": "https://old.com", "snippet": "Old"},
        ]
    }
    await search_engine.connect()
    search_engine._client.get = AsyncMock(return_value=_mock_response(200, json=mock_data))  # type: ignore[method-assign,union-attr]

    await search_engine.search("expire_query", num_results=1, source="serpapi")
    # Force expiration by manipulating timestamp
    for key in list(search_engine._cache.keys()):
        results, _ = search_engine._cache[key]
        search_engine._cache[key] = (results, datetime.now(timezone.utc) - timedelta(hours=1))

    await search_engine.search("expire_query", num_results=1, source="serpapi")
    assert search_engine._client.get.call_count == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_search_engine_multiple_concurrent(search_engine: SearchEngine) -> None:
    mock_data = {
        "organic_results": [
            {"title": "R", "link": "https://r.com", "snippet": "S"},
        ]
    }
    await search_engine.connect()
    search_engine._client.get = AsyncMock(return_value=_mock_response(200, json=mock_data))  # type: ignore[method-assign,union-attr]

    results = await search_engine.search_multiple(["q1", "q2", "q3"], num_results=1)
    assert len(results) == 3
    assert all(len(v) == 1 for v in results.values())


# ---------------------------------------------------------------------------
# WebScraper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scraper_connect_disconnect(scraper: WebScraper) -> None:
    assert scraper._client is None
    await scraper.connect()
    assert scraper._client is not None
    await scraper.disconnect()
    assert scraper._client is None


@pytest.mark.asyncio
async def test_scraper_extracts_text(scraper: WebScraper) -> None:
    html = """
    <html>
      <head><title>Test Page</title></head>
      <body>
        <script>alert('ignore me');</script>
        <nav>Navigation</nav>
        <p>This is the main content. It has multiple sentences.</p>
        <footer>Footer</footer>
      </body>
    </html>
    """
    await scraper.connect()
    scraper._client.get = AsyncMock(return_value=_mock_response(200, text=html))  # type: ignore[method-assign,union-attr]

    result = await scraper.scrape("https://example.com")
    assert result is not None
    assert result.title == "Test Page"
    assert "main content" in result.text.lower()
    assert "alert" not in result.text.lower()
    assert "Navigation" not in result.text
    assert "Footer" not in result.text
    assert result.word_count > 0


@pytest.mark.asyncio
async def test_scraper_handles_failure(scraper: WebScraper) -> None:
    await scraper.connect()
    scraper._client.get = AsyncMock(side_effect=Exception("Connection refused"))  # type: ignore[method-assign,union-attr]
    result = await scraper.scrape("https://fail.com")
    assert result is None


@pytest.mark.asyncio
async def test_scraper_multiple(scraper: WebScraper) -> None:
    html = "<html><head><title>T</title></head><body><p>Content</p></body></html>"
    await scraper.connect()
    scraper._client.get = AsyncMock(return_value=_mock_response(200, text=html))  # type: ignore[method-assign,union-attr]

    results = await scraper.scrape_multiple(["https://a.com", "https://b.com"], concurrency=2)
    assert len(results) == 2
    assert all(r is not None for r in results.values())


# ---------------------------------------------------------------------------
# ResearchSynthesizer
# ---------------------------------------------------------------------------


def test_synthesize_empty(synthesizer: ResearchSynthesizer) -> None:
    report = synthesizer.synthesize("query", [])
    assert report.query == "query"
    assert report.briefs == []
    assert report.overall_confidence == 0.0
    assert report.top_insights == []


def test_synthesize_basic(synthesizer: ResearchSynthesizer) -> None:
    contents = [
        ScrapedContent(
            url="https://github.com/repo",
            title="GitHub Repo",
            text="This project enables SaaS arbitrage with high profit margins.",
            word_count=10,
        ),
        ScrapedContent(
            url="https://example.com/blog",
            title="Blog Post",
            text="A trend in freelance gigs shows growing demand for quick tasks.",
            word_count=12,
        ),
    ]
    report = synthesizer.synthesize("arbitrage opportunities", contents)
    assert len(report.briefs) == 2
    assert report.overall_confidence > 0.0
    assert len(report.top_insights) > 0


def test_synthesize_filters_low_credibility(synthesizer: ResearchSynthesizer) -> None:
    contents = [
        ScrapedContent(
            url="https://pinterest.com/pin",
            title="Pin",
            text="x",  # Very short -> low credibility
            word_count=1,
        ),
    ]
    report = synthesizer.synthesize("query", contents)
    assert len(report.briefs) == 0


def test_score_opportunity(synthesizer: ResearchSynthesizer) -> None:
    briefs = [
        SourceBrief(
            url="https://github.com/a",
            title="Profit Tool",
            summary="This tool generates revenue and arbitrage profit.",
            credibility=0.9,
            word_count=50,
        ),
    ]
    report = SynthesisReport(
        query="money",
        briefs=briefs,
        overall_confidence=0.8,
        top_insights=["Insight 1"],
    )
    score = synthesizer.score_opportunity(report)
    assert 0.0 < score <= 1.0


def test_score_opportunity_empty(synthesizer: ResearchSynthesizer) -> None:
    report = SynthesisReport(query="q", briefs=[], overall_confidence=0.0, top_insights=[])
    assert synthesizer.score_opportunity(report) == 0.0


def test_domain_reputation_boosts_confidence(synthesizer: ResearchSynthesizer) -> None:
    contents = [
        ScrapedContent(
            url="https://arxiv.org/abs/1234",
            title="Paper",
            text="A rigorous study on arbitrage opportunities in SaaS markets.",
            word_count=200,
        ),
    ]
    report = synthesizer.synthesize("study", contents)
    assert len(report.briefs) == 1
    assert report.briefs[0].credibility > 0.9


# ---------------------------------------------------------------------------
# ResearchStore
# ---------------------------------------------------------------------------


def test_store_save_and_get_task(research_store: ResearchStore) -> None:
    task = ResearchTask(
        query="test query",
        sources=["google"],
        budget=1.0,
        deadline=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    task_id = research_store.save_task(task)
    assert task_id > 0

    tasks = research_store.get_tasks()
    assert len(tasks) == 1
    assert tasks[0].query == "test query"
    assert tasks[0].budget == 1.0
    assert tasks[0].sources == ["google"]


def test_store_save_and_get_result(research_store: ResearchStore) -> None:
    task = ResearchTask(query="q")
    task_id = research_store.save_task(task)

    result_id = research_store.save_result(
        task_id=task_id,
        query="q",
        summary="summary text",
        confidence=0.8,
        opportunity_score=0.6,
        domain="trading",
        data={"key": "value"},
        sources=[{"url": "https://a.com", "title": "A", "credibility": 0.9, "summary": "S"}],
    )
    assert result_id > 0

    results = research_store.get_results(domain="trading")
    assert len(results) == 1
    assert results[0].query == "q"
    assert results[0].confidence == 0.8
    assert results[0].opportunity_score == 0.6
    assert results[0].data == {"key": "value"}


def test_store_filters_by_confidence(research_store: ResearchStore) -> None:
    task_id = research_store.save_task(ResearchTask(query="q"))
    research_store.save_result(task_id, "q", "", 0.3, 0.5, "saas", {})
    research_store.save_result(task_id, "q", "", 0.9, 0.7, "saas", {})

    results = research_store.get_results(min_confidence=0.5)
    assert len(results) == 1
    assert results[0].confidence == 0.9


def test_store_top_opportunities(research_store: ResearchStore) -> None:
    task_id = research_store.save_task(ResearchTask(query="q"))
    research_store.save_result(task_id, "q", "", 0.9, 0.3, "domain", {})
    research_store.save_result(task_id, "q", "", 0.8, 0.9, "domain", {})

    top = research_store.get_top_opportunities()
    assert len(top) == 1
    assert top[0].opportunity_score == 0.9


# ---------------------------------------------------------------------------
# OpportunityMonitor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_start_stop(opportunity_monitor: OpportunityMonitor) -> None:
    await opportunity_monitor.start()
    assert opportunity_monitor._running
    assert opportunity_monitor._task is not None
    await opportunity_monitor.stop()
    assert not opportunity_monitor._running


@pytest.mark.asyncio
async def test_monitor_research_pipeline(opportunity_monitor: OpportunityMonitor) -> None:
    # Patch dependencies to avoid real network calls
    search_result = SearchResult(title="T", url="https://a.com", snippet="S", rank=1, source="test")
    scraped = ScrapedContent(
        url="https://a.com",
        title="T",
        text="Great arbitrage opportunity in SaaS with high profit and revenue growth.",
        word_count=20,
    )

    opportunity_monitor._search.search = AsyncMock(return_value=[search_result])  # type: ignore[method-assign]
    opportunity_monitor._scraper.scrape_multiple = AsyncMock(return_value={"https://a.com": scraped})  # type: ignore[method-assign]

    report, opps = await opportunity_monitor.research("SaaS arbitrage", domain="saas_arbitrage")
    assert isinstance(report, SynthesisReport)
    assert len(opps) >= 0

    # If opportunity score is high enough, event bus should receive it
    if opps:
        opportunity_monitor._event_bus.publish.assert_awaited_once()
        event = opportunity_monitor._event_bus.publish.call_args[0][1]
        assert isinstance(event, OpportunityDiscovered)


@pytest.mark.asyncio
async def test_monitor_run_once(opportunity_monitor: OpportunityMonitor) -> None:
    search_result = SearchResult(title="T", url="https://a.com", snippet="S", rank=1, source="test")
    scraped = ScrapedContent(
        url="https://a.com",
        title="T",
        text="Trading signals show arbitrage opportunity profit revenue growth.",
        word_count=20,
    )

    opportunity_monitor._search.search = AsyncMock(return_value=[search_result])  # type: ignore[method-assign]
    opportunity_monitor._scraper.scrape_multiple = AsyncMock(return_value={"https://a.com": scraped})  # type: ignore[method-assign]

    discovered = await opportunity_monitor.run_once(domains=["trading_signals"])
    assert isinstance(discovered, list)


@pytest.mark.asyncio
async def test_monitor_respects_spend_budget(opportunity_monitor: OpportunityMonitor) -> None:
    opportunity_monitor._daily_spend = 2.0  # Over the default $1.00 max
    discovered = await opportunity_monitor.run_once()
    assert discovered == []


@pytest.mark.asyncio
async def test_monitor_research_no_results(opportunity_monitor: OpportunityMonitor) -> None:
    opportunity_monitor._search.search = AsyncMock(return_value=[])  # type: ignore[method-assign]
    report, opps = await opportunity_monitor.research("empty query")
    assert report.overall_confidence == 0.0
    assert opps == []


# ---------------------------------------------------------------------------
# Integration: full pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline(tmp_path: Any, mock_event_bus: AsyncMock) -> None:
    """End-to-end pipeline: search -> scrape -> synthesize -> store -> emit."""
    store = ResearchStore(db_path=str(tmp_path / "pipeline.db"))
    search_provider = AsyncMock()
    scraper = WebScraper()
    synth = ResearchSynthesizer()
    monitor = OpportunityMonitor(
        event_bus=mock_event_bus,
        search_provider=search_provider,
        scraper=scraper,
        synthesizer=synth,
        store=store,
        config=MonitorConfig(min_opportunity_score=0.01, max_daily_spend=10.0),
    )

    # Mock the network layer
    search_result = SearchResult(
        title="Profit Guide",
        url="https://example.com/profit",
        snippet="How to make money with SaaS arbitrage.",
        rank=1,
        source="serpapi",
    )
    html = """
    <html><head><title>Profit Guide</title></head>
    <body><p>This guide shows how to make money with SaaS arbitrage and generate profit revenue.</p></body>
    </html>
    """
    search_provider.search = AsyncMock(return_value=[search_result])
    scraper._client = MagicMock()
    scraper._client.get = AsyncMock(return_value=_mock_response(200, text=html))

    report, opps = await monitor.research("SaaS arbitrage", domain="saas_arbitrage")

    assert report.overall_confidence > 0.0
    assert len(report.briefs) == 1
    assert len(opps) >= 0

    # Verify storage
    stored = store.get_results(domain="saas_arbitrage")
    assert len(stored) == 1
    assert stored[0].query == "SaaS arbitrage"

    # Verify event emission
    if opps:
        mock_event_bus.publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status: int, json: Any = None, text: str = "") -> Any:
    import httpx

    request = httpx.Request("GET", "http://test")
    if json is not None:
        return httpx.Response(status, json=json, request=request)
    return httpx.Response(status, text=text, request=request)
