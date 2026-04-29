"""Intelligence gathering and research engine for Project ÆON."""

from __future__ import annotations

from auton.senses.intelligence.duckduckgo_search import DuckDuckGoSearchProvider
from auton.senses.intelligence.opportunity_monitor import OpportunityMonitor
from auton.senses.intelligence.scraper import ScrapedContent, WebScraper
from auton.senses.intelligence.search_engine import SearchEngine
from auton.senses.intelligence.search_provider import (
    SearchProvider,
    SearchResult,
    create_search_provider,
)
from auton.senses.intelligence.serpapi_search import SerpAPISearchProvider
from auton.senses.intelligence.storage import ResearchStore
from auton.senses.intelligence.synthesizer import ResearchSynthesizer, SourceBrief, SynthesisReport

__all__ = [
    "SearchEngine",
    "SearchProvider",
    "SearchResult",
    "SerpAPISearchProvider",
    "DuckDuckGoSearchProvider",
    "create_search_provider",
    "WebScraper",
    "ScrapedContent",
    "ResearchSynthesizer",
    "SourceBrief",
    "SynthesisReport",
    "OpportunityMonitor",
    "ResearchStore",
]
