"""Senses data ingestion framework for Project ÆON."""

from auton.senses.base_connector import BaseConnector, DataReceived
from auton.senses.dataclasses import Candle, MarketData, OrderBook, SentimentScore
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
]
