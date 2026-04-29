"""Shared dataclasses for simulation connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SimulatedData:
    """A wrapper that marks real data as simulated.

    All simulation connectors return :class:`SimulatedData` so downstream
    code can distinguish production payloads from sandboxed ones.
    """

    source: str
    data_type: str
    payload: Any
    sim_time: datetime
    simulated: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
