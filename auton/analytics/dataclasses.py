from dataclasses import dataclass


@dataclass(frozen=True)
class AlphaSignal:
    direction: str
    strength: float
    confidence: float
    expected_horizon: str


@dataclass(frozen=True)
class RevenueOpportunity:
    niche: str
    expected_roi: float
    risk_score: float
    time_to_capture: str
    confidence: float


@dataclass(frozen=True)
class RiskAssessment:
    position_size_pct: float
    max_drawdown_pct: float
    survival_reserve: float
    kelly_fraction: float
    tier_cap: float
    approved: bool


@dataclass(frozen=True)
class BacktestResult:
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    trades_executed: int
