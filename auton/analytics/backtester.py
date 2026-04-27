import numpy as np

from .dataclasses import BacktestResult


class Backtester:
    def run_backtest(
        self,
        strategy: callable,
        historical_data: np.ndarray,
        initial_balance: float,
        cost_per_trade: float,
    ) -> BacktestResult:
        balance = initial_balance
        peak_balance = initial_balance
        trades = []
        position = 0.0
        entry_price = 0.0

        for i in range(len(historical_data)):
            signal = strategy(historical_data[: i + 1])
            price = float(historical_data[i])

            if signal == "buy" and position == 0.0 and balance > cost_per_trade:
                position = balance / price
                entry_price = price
                balance = 0.0
                balance -= cost_per_trade
                trades.append({"type": "buy", "price": price})
            elif signal == "sell" and position > 0.0 and balance >= -cost_per_trade:
                balance += position * price - cost_per_trade
                trades.append({"type": "sell", "price": price})
                position = 0.0
                entry_price = 0.0

            current_value = balance + position * price
            if current_value > peak_balance:
                peak_balance = current_value

        if position > 0:
            balance += position * float(historical_data[-1])
            position = 0.0

        final_value = balance
        total_return = (final_value - initial_balance) / initial_balance if initial_balance > 0 else 0.0

        winning_trades = 0
        for i in range(1, len(trades), 2):
            if i < len(trades):
                buy_price = trades[i - 1]["price"]
                sell_price = trades[i]["price"]
                if sell_price > buy_price:
                    winning_trades += 1

        num_round_trips = len(trades) // 2
        win_rate = winning_trades / num_round_trips if num_round_trips > 0 else 0.0

        max_drawdown = self._compute_max_drawdown(historical_data, initial_balance, strategy, cost_per_trade)

        returns = self._compute_periodic_returns(historical_data, initial_balance, strategy, cost_per_trade)
        sharpe_ratio = self._compute_sharpe(returns)

        return BacktestResult(
            total_return=total_return,
            sharpe_ratio=sharpe_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            trades_executed=len(trades),
        )

    def _compute_periodic_returns(
        self,
        historical_data: np.ndarray,
        initial_balance: float,
        strategy: callable,
        cost_per_trade: float,
    ) -> np.ndarray:
        balance = initial_balance
        position = 0.0
        values = np.empty(len(historical_data))

        for i in range(len(historical_data)):
            signal = strategy(historical_data[: i + 1])
            price = float(historical_data[i])

            if signal == "buy" and position == 0.0 and balance > cost_per_trade:
                position = balance / price
                balance = 0.0
                balance -= cost_per_trade
            elif signal == "sell" and position > 0.0 and balance >= -cost_per_trade:
                balance += position * price - cost_per_trade
                position = 0.0

            values[i] = balance + position * price

        if position > 0:
            values[-1] = balance + position * float(historical_data[-1])

        returns = np.diff(values) / values[:-1]
        returns = returns[~np.isnan(returns) & ~np.isinf(returns) & (values[:-1] != 0)]
        return returns if len(returns) > 0 else np.array([0.0])

    def _compute_max_drawdown(
        self,
        historical_data: np.ndarray,
        initial_balance: float,
        strategy: callable,
        cost_per_trade: float,
    ) -> float:
        balance = initial_balance
        position = 0.0
        peak = initial_balance
        max_dd = 0.0

        for i in range(len(historical_data)):
            signal = strategy(historical_data[: i + 1])
            price = float(historical_data[i])

            if signal == "buy" and position == 0.0 and balance > cost_per_trade:
                position = balance / price
                balance = 0.0
                balance -= cost_per_trade
            elif signal == "sell" and position > 0.0 and balance >= -cost_per_trade:
                balance += position * price - cost_per_trade
                position = 0.0

            current = balance + position * price
            if current > peak:
                peak = current
            if peak > 0:
                dd = (peak - current) / peak
                if dd > max_dd:
                    max_dd = dd

        return max_dd

    def _compute_sharpe(self, returns: np.ndarray, risk_free_rate: float = 0.0) -> float:
        if len(returns) == 0 or np.std(returns) == 0:
            return 0.0
        excess = returns - risk_free_rate
        return float(np.mean(excess) / np.std(excess))
