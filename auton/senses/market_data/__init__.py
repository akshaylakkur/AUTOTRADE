"""Market data connectors for the Senses framework."""

from auton.senses.market_data.binance_spot import BinanceSpotConnector
from auton.senses.market_data.coinbase_pro import CoinbaseProConnector

__all__ = ["BinanceSpotConnector", "CoinbaseProConnector"]
