"""Project ÆON Simulation Engine.

Provides a controlled, deterministic environment for backtesting
strategies without risking real capital.
"""

from auton.simulation.analyzer import SimulationAnalyzer
from auton.simulation.clock import SimulationClock
from auton.simulation.recorder import SimulationRecorder
from auton.simulation.session import SimulationSession
from auton.simulation.wallet import SimulatedWallet

__all__ = [
    "SimulationAnalyzer",
    "SimulationClock",
    "SimulationRecorder",
    "SimulationSession",
    "SimulatedWallet",
]
