"""Simulation connectors — real-world data, simulated money.

These wrappers let AEON test strategies against real data and APIs
without risking capital. Every action is tagged as simulated and
recorded for later analysis.
"""

from __future__ import annotations

from auton.simulation.connectors.commerce import CommerceSimulator
from auton.simulation.connectors.cost_estimator import CostEstimator
from auton.simulation.connectors.dataclasses import SimulatedData
from auton.simulation.connectors.market_data import MarketDataSimulator
from auton.simulation.connectors.web_research import WebResearchSimulator

__all__ = [
    "SimulatedData",
    "MarketDataSimulator",
    "WebResearchSimulator",
    "CommerceSimulator",
    "CostEstimator",
]
