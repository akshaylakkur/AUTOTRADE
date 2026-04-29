"""CostEstimator — estimates real costs using external APIs and fee schedules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from auton.senses.base_connector import BaseConnector
from auton.simulation.clock import SimulationClock
from auton.simulation.connectors.dataclasses import SimulatedData
from auton.simulation.recorder import SimulationRecorder


class CostEstimator:
    """Estimates operational costs for trading, compute, and data.

    Uses hard-coded fee schedules (exchange maker/taker rates, LLM
    token pricing) and connector subscription costs. Results are wrapped
    in :class:`SimulatedData` and recorded for budget planning inside
    simulations.

    In a future iteration this module can query live pricing APIs
    (e.g. OpenAI pricing endpoint, exchange fee tiers) for up-to-date
    estimates.
    """

    # Exchange fee schedules (maker, taker)
    _EXCHANGE_FEES: dict[str, dict[str, float]] = {
        "binance": {"maker": 0.001, "taker": 0.001},
        "coinbase": {"maker": 0.004, "taker": 0.006},
        "kraken": {"maker": 0.0016, "taker": 0.0026},
    }

    # LLM token pricing per 1K tokens (input, output) in USD
    _LLM_PRICING: dict[str, dict[str, float]] = {
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
        "claude-3-opus": {"input": 0.015, "output": 0.075},
        "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    }

    # Generic compute per-hour estimates (USD)
    _COMPUTE_HOURLY: dict[str, float] = {
        "cpu-small": 0.05,
        "cpu-medium": 0.20,
        "gpu-small": 1.50,
        "gpu-medium": 4.00,
    }

    def __init__(
        self,
        recorder: SimulationRecorder | None = None,
        clock: SimulationClock | None = None,
    ) -> None:
        self._recorder = recorder
        self._clock = clock

    # ------------------------------------------------------------------
    # Trading cost estimates
    # ------------------------------------------------------------------

    def estimate_trading_cost(
        self,
        symbol: str,
        quantity: float,
        price: float,
        exchange: str = "binance",
        side: str = "taker",
    ) -> SimulatedData:
        """Estimate trading fee for a hypothetical order.

        Args:
            symbol: Trading pair, e.g. ``BTCUSDT``.
            quantity: Order quantity.
            price: Order price (used to compute notional value).
            exchange: Exchange key (``binance``, ``coinbase``, ``kraken``).
            side: ``maker`` or ``taker``.
        """
        schedule = self._EXCHANGE_FEES.get(exchange, {"taker": 0.001})
        fee_rate = schedule.get(side, schedule.get("taker", 0.001))
        notional = quantity * price
        cost = notional * fee_rate

        sim = SimulatedData(
            source="cost_estimator",
            data_type="trading_cost_estimate",
            payload={
                "exchange": exchange,
                "symbol": symbol,
                "notional": notional,
                "fee_rate": fee_rate,
                "estimated_cost": cost,
                "side": side,
            },
            sim_time=self._sim_time(),
            metadata={"action": "estimate_trading_cost"},
        )
        self._record("cost", "trading_estimate", sim.payload)
        return sim

    # ------------------------------------------------------------------
    # Compute cost estimates
    # ------------------------------------------------------------------

    def estimate_llm_cost(
        self,
        model: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> SimulatedData:
        """Estimate cost for an LLM inference call.

        Args:
            model: Model identifier (e.g. ``gpt-4``, ``claude-3-sonnet``).
            tokens_in: Number of input tokens.
            tokens_out: Number of output tokens.
        """
        rates = self._LLM_PRICING.get(model, {"input": 0.03, "output": 0.06})
        cost = (
            (tokens_in / 1000.0) * rates["input"]
            + (tokens_out / 1000.0) * rates["output"]
        )

        sim = SimulatedData(
            source="cost_estimator",
            data_type="llm_cost_estimate",
            payload={
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "estimated_cost": cost,
            },
            sim_time=self._sim_time(),
            metadata={"action": "estimate_llm_cost"},
        )
        self._record("cost", "llm_estimate", sim.payload)
        return sim

    def estimate_compute_cost(
        self,
        instance_type: str = "cpu-small",
        hours: float = 1.0,
    ) -> SimulatedData:
        """Estimate hourly compute cost.

        Args:
            instance_type: Key from the internal compute price table.
            hours: Number of hours the instance runs.
        """
        rate = self._COMPUTE_HOURLY.get(instance_type, 0.05)
        cost = rate * hours

        sim = SimulatedData(
            source="cost_estimator",
            data_type="compute_cost_estimate",
            payload={
                "instance_type": instance_type,
                "hours": hours,
                "hourly_rate": rate,
                "estimated_cost": cost,
            },
            sim_time=self._sim_time(),
            metadata={"action": "estimate_compute_cost"},
        )
        self._record("cost", "compute_estimate", sim.payload)
        return sim

    # ------------------------------------------------------------------
    # Data cost estimates
    # ------------------------------------------------------------------

    def estimate_data_cost(self, connector: BaseConnector) -> SimulatedData:
        """Return the subscription cost for a data connector.

        Args:
            connector: A production :class:`BaseConnector` instance.
        """
        cost = connector.get_subscription_cost()
        sim = SimulatedData(
            source="cost_estimator",
            data_type="data_cost_estimate",
            payload={
                "connector": connector.__class__.__name__,
                "cost": cost,
            },
            sim_time=self._sim_time(),
            metadata={"action": "estimate_data_cost"},
        )
        self._record("cost", "data_estimate", sim.payload)
        return sim

    # ------------------------------------------------------------------
    # Aggregate estimates
    # ------------------------------------------------------------------

    def estimate_daily_burn(
        self,
        connectors: list[BaseConnector] | None = None,
        trades_per_day: int = 5,
        avg_notional: float = 100.0,
        exchange: str = "binance",
        llm_calls: int = 10,
        llm_model: str = "gpt-3.5-turbo",
        llm_tokens_in: int = 2000,
        llm_tokens_out: int = 500,
        compute_hours: float = 24.0,
        compute_type: str = "cpu-small",
    ) -> SimulatedData:
        """Aggregate daily burn estimate from multiple cost categories."""
        data_total = 0.0
        conn_breakdown: dict[str, float] = {}
        if connectors:
            for conn in connectors:
                cost = conn.get_subscription_cost()
                daily = cost.get("daily", 0.0)
                data_total += daily
                conn_breakdown[conn.__class__.__name__] = daily

        trading = self.estimate_trading_cost(
            symbol="AGGREGATE", quantity=1.0, price=avg_notional,
            exchange=exchange, side="taker",
        )
        trading_total = trading.payload["estimated_cost"] * trades_per_day

        llm = self.estimate_llm_cost(
            model=llm_model, tokens_in=llm_tokens_in, tokens_out=llm_tokens_out,
        )
        llm_total = llm.payload["estimated_cost"] * llm_calls

        compute = self.estimate_compute_cost(
            instance_type=compute_type, hours=compute_hours,
        )
        compute_total = compute.payload["estimated_cost"]

        total = data_total + trading_total + llm_total + compute_total

        sim = SimulatedData(
            source="cost_estimator",
            data_type="daily_burn_estimate",
            payload={
                "total": total,
                "breakdown": {
                    "data": data_total,
                    "trading": trading_total,
                    "llm": llm_total,
                    "compute": compute_total,
                },
                "details": {
                    "data_connectors": conn_breakdown,
                    "trades_per_day": trades_per_day,
                    "llm_calls": llm_calls,
                    "compute_hours": compute_hours,
                },
            },
            sim_time=self._sim_time(),
            metadata={"action": "estimate_daily_burn"},
        )
        self._record("cost", "daily_burn_estimate", sim.payload)
        return sim

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sim_time(self) -> datetime:
        if self._clock is not None:
            return self._clock.now()
        return datetime.now(timezone.utc)

    def _record(self, category: str, action: str, payload: dict[str, Any]) -> None:
        if self._recorder is not None:
            self._recorder.record(self._sim_time(), category, action, payload)
