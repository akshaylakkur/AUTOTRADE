"""Burn Rate Analyzer — runway and time-to-zero projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import overload


@dataclass(frozen=True)
class RunwayReport:
    """Immutable snapshot of a runway calculation."""

    balance: float
    daily_burn_rate: float
    daily_income: float
    net_daily_burn: float
    runway_days: float
    runway_hours: float
    runway_timedelta: timedelta


class BurnAnalyzer:
    """Project how long the treasury will last given burn and (optional) income."""

    @overload
    def project_time_to_death(
        self,
        current_balance: float,
        daily_burn_rate: float,
    ) -> timedelta: ...

    @overload
    def project_time_to_death(
        self,
        current_balance: float,
        daily_burn_rate: float,
        daily_income: float,
    ) -> timedelta: ...

    def project_time_to_death(
        self,
        current_balance: float,
        daily_burn_rate: float,
        daily_income: float = 0.0,
    ) -> timedelta:
        """Return a :class:`timedelta` until the balance reaches zero.

        Args:
            current_balance: Treasury balance today.
            daily_burn_rate: Absolute daily spend (must be >= 0).
            daily_income: Absolute daily income (must be >= 0).

        Raises:
            ValueError: If burn rate or income are negative.
        """
        if daily_burn_rate < 0 or daily_income < 0:
            raise ValueError("burn rate and income must be non-negative")
        net = daily_burn_rate - daily_income
        if net <= 0:
            return timedelta.max
        days = current_balance / net
        return timedelta(days=days)

    def get_burn_rate(
        self,
        daily_costs: list[float],
    ) -> float:
        """Return the average daily burn over the supplied cost history.

        Args:
            daily_costs: A list of total costs per day (chronological order).
        """
        if not daily_costs:
            return 0.0
        return sum(daily_costs) / len(daily_costs)

    def get_runway(
        self,
        balance: float,
        daily_burn_rate: float,
        daily_income: float = 0.0,
    ) -> RunwayReport:
        """Return a detailed :class:`RunwayReport`.

        Args:
            balance: Current treasury balance.
            daily_burn_rate: Average daily spend.
            daily_income: Average daily income (default 0).
        """
        if daily_burn_rate < 0 or daily_income < 0:
            raise ValueError("burn rate and income must be non-negative")

        net = daily_burn_rate - daily_income
        if net <= 0:
            runway_days = float("inf")
            runway_hours = float("inf")
            td = timedelta.max
        else:
            runway_days = balance / net
            runway_hours = runway_days * 24.0
            td = timedelta(days=runway_days)

        return RunwayReport(
            balance=balance,
            daily_burn_rate=daily_burn_rate,
            daily_income=daily_income,
            net_daily_burn=net,
            runway_days=runway_days,
            runway_hours=runway_hours,
            runway_timedelta=td,
        )
