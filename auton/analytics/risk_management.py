import numpy as np

from .dataclasses import RiskAssessment


class RiskManager:
    TIER_CAPS = {
        0: 0.02,
        1: 0.02,
        2: 0.02,
        3: 0.01,
        4: 0.01,
    }

    SURVIVAL_RESERVE_PCT = 0.10

    def kelly_criterion(self, win_prob: float, win_loss_ratio: float) -> float:
        if win_loss_ratio <= 0 or win_prob <= 0:
            return 0.0
        kelly = (win_prob * win_loss_ratio - (1 - win_prob)) / win_loss_ratio
        return max(0.0, min(kelly, 1.0))

    def correlation_heatmap(self, returns_matrix: np.ndarray) -> np.ndarray:
        if returns_matrix.ndim != 2:
            raise ValueError("returns_matrix must be 2D")

        valid_mask = ~np.isnan(returns_matrix).all(axis=0)
        valid_returns = returns_matrix[:, valid_mask]

        if valid_returns.shape[1] == 0:
            return np.array([])

        return np.corrcoef(valid_returns, rowvar=False)

    def check_drawdown(self, current_balance: float, peak_balance: float) -> float:
        if peak_balance <= 0:
            return 0.0
        return max(0.0, (peak_balance - current_balance) / peak_balance)

    def enforce_survival_reserve(self, balance: float) -> float:
        return balance * self.SURVIVAL_RESERVE_PCT

    def max_position_size(self, balance: float, tier: int, edge: float) -> RiskAssessment:
        survival_reserve = self.enforce_survival_reserve(balance)
        tradable_balance = balance - survival_reserve

        if tradable_balance <= 0:
            return RiskAssessment(
                position_size_pct=0.0,
                max_drawdown_pct=0.0,
                survival_reserve=survival_reserve,
                kelly_fraction=0.0,
                tier_cap=0.0,
                approved=False,
            )

        kelly = self.kelly_criterion(edge, 1.5)
        tier_cap = self.TIER_CAPS.get(tier, 0.01)

        raw_size = kelly * tradable_balance
        capped_size = min(raw_size, tier_cap * balance)
        position_size_pct = capped_size / balance if balance > 0 else 0.0

        max_dd = self.check_drawdown(balance, balance)
        approved = position_size_pct > 0 and position_size_pct <= tier_cap

        return RiskAssessment(
            position_size_pct=position_size_pct,
            max_drawdown_pct=max_dd,
            survival_reserve=survival_reserve,
            kelly_fraction=kelly,
            tier_cap=tier_cap,
            approved=approved,
        )
