"""Intelligence gathering and research engine for Project ÆON."""

from __future__ import annotations

from auton.senses.intelligence.opportunity_monitor import OpportunityMonitor
from auton.senses.intelligence.scraper import ScrapedContent, WebScraper
from auton.senses.intelligence.search_engine import SearchEngine, SearchResult
from auton.senses.intelligence.storage import ResearchStore
from auton.senses.intelligence.synthesizer import ResearchSynthesizer, SourceBrief, SynthesisReport

__all__ = [
    "SearchEngine",
    "SearchResult",
    "WebScraper",
    "ScrapedContent",
    "ResearchSynthesizer",
    "SourceBrief",
    "SynthesisReport",
    "OpportunityMonitor",
    "ResearchStore",
]
