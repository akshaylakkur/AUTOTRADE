"""Senses data ingestion framework for Project ÆON."""

from auton.senses.base_connector import BaseConnector, DataReceived
from auton.senses.dataclasses import Candle, MarketData, OrderBook, SentimentScore
from auton.senses.environment import (
    ContextSnapshot,
    EconomicEvent,
    EnvironmentalSensor,
    ImpactLevel,
    MarketSession,
)
from auton.senses.intelligence import (
    OpportunityMonitor,
    ResearchStore,
    ResearchSynthesizer,
    ScrapedContent,
    SearchEngine,
    SearchResult,
    SourceBrief,
    SynthesisReport,
    WebScraper,
)
from auton.senses.market_data import BinanceSpotConnector, CoinbaseProConnector
from auton.senses.sentiment import TwitterSentimentConnector

__all__ = [
    "BaseConnector",
    "DataReceived",
    "BinanceSpotConnector",
    "CoinbaseProConnector",
    "TwitterSentimentConnector",
    "MarketData",
    "OrderBook",
    "Candle",
    "SentimentScore",
    "SearchEngine",
    "SearchResult",
    "WebScraper",
    "ScrapedContent",
    "ResearchSynthesizer",
    "SourceBrief",
    "SynthesisReport",
    "OpportunityMonitor",
    "ResearchStore",
    "EnvironmentalSensor",
    "ContextSnapshot",
    "EconomicEvent",
    "MarketSession",
    "ImpactLevel",
]
