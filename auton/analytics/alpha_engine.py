import numpy as np
from scipy import stats

from .dataclasses import AlphaSignal


class AlphaEngine:
    def technical_analysis(self, prices: np.ndarray) -> AlphaSignal:
        if len(prices) < 30:
            return AlphaSignal(direction="neutral", strength=0.0, confidence=0.0, expected_horizon="short")

        if np.std(prices) < 1e-9:
            return AlphaSignal(direction="neutral", strength=0.0, confidence=1.0, expected_horizon="short")

        rsi = self._compute_rsi(prices)
        macd_line, signal_line = self._compute_macd(prices)
        upper, lower = self._compute_bollinger_bands(prices)

        latest_price = float(prices[-1])
        signals = []

        if rsi < 30:
            signals.append("bullish")
        elif rsi > 70:
            signals.append("bearish")
        else:
            signals.append("neutral")

        if macd_line[-1] > signal_line[-1] and macd_line[-2] <= signal_line[-2]:
            signals.append("bullish")
        elif macd_line[-1] < signal_line[-1] and macd_line[-2] >= signal_line[-2]:
            signals.append("bearish")
        else:
            signals.append("neutral")

        if latest_price < lower[-1]:
            signals.append("bullish")
        elif latest_price > upper[-1]:
            signals.append("bearish")
        else:
            signals.append("neutral")

        bullish_count = signals.count("bullish")
        bearish_count = signals.count("bearish")
        neutral_count = signals.count("neutral")

        if bullish_count > bearish_count:
            direction = "bullish"
        elif bearish_count > bullish_count:
            direction = "bearish"
        else:
            direction = "neutral"

        strength = max(bullish_count, bearish_count) / len(signals)
        confidence = 1.0 - (neutral_count / len(signals))

        return AlphaSignal(
            direction=direction,
            strength=strength,
            confidence=confidence,
            expected_horizon="short",
        )

    def _compute_rsi(self, prices: np.ndarray, period: int = 14) -> float:
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _compute_macd(self, prices: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray]:
        ema_fast = self._ema(prices, fast)
        ema_slow = self._ema(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = self._ema(macd_line, signal)
        return macd_line, signal_line

    def _ema(self, data: np.ndarray, span: int) -> np.ndarray:
        alpha = 2.0 / (span + 1)
        ema = np.empty_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema

    def _compute_bollinger_bands(self, prices: np.ndarray, period: int = 20, std_dev: int = 2) -> tuple[np.ndarray, np.ndarray]:
        sma = np.convolve(prices, np.ones(period) / period, mode="valid")
        rolling_std = np.array([np.std(prices[i : i + period]) for i in range(len(prices) - period + 1)])
        upper = sma + std_dev * rolling_std
        lower = sma - std_dev * rolling_std
        padded_upper = np.concatenate([np.full(period - 1, np.nan), upper])
        padded_lower = np.concatenate([np.full(period - 1, np.nan), lower])
        return padded_upper, padded_lower

    def stat_arb(self, pair_prices: np.ndarray) -> AlphaSignal:
        if pair_prices.shape[1] != 2 or len(pair_prices) < 30:
            return AlphaSignal(direction="neutral", strength=0.0, confidence=0.0, expected_horizon="medium")

        x = pair_prices[:, 0]
        y = pair_prices[:, 1]

        spread = y - x
        spread_mean = np.mean(spread)
        spread_std = np.std(spread)

        if spread_std == 0:
            return AlphaSignal(direction="neutral", strength=0.0, confidence=0.0, expected_horizon="medium")

        z_score = (spread[-1] - spread_mean) / spread_std

        slope, intercept, r_value, _, _ = stats.linregress(x, y)
        residuals = y - (slope * x + intercept)
        adf_stat = self._approximate_adf(residuals)

        if z_score > 2.0 and adf_stat < -2.0:
            direction = "short_spread"
        elif z_score < -2.0 and adf_stat < -2.0:
            direction = "long_spread"
        else:
            direction = "neutral"

        strength = min(abs(z_score) / 4.0, 1.0)
        confidence = min(abs(r_value), 1.0)

        return AlphaSignal(
            direction=direction,
            strength=strength,
            confidence=confidence,
            expected_horizon="medium",
        )

    def _approximate_adf(self, residuals: np.ndarray) -> float:
        diff = np.diff(residuals)
        lagged = residuals[:-1]
        slope, _, _, _, _ = stats.linregress(lagged, diff)
        return slope * 100

    def sentiment_alpha(self, sentiment_scores: np.ndarray) -> AlphaSignal:
        if len(sentiment_scores) == 0:
            return AlphaSignal(direction="neutral", strength=0.0, confidence=0.0, expected_horizon="short")

        mean_sentiment = float(np.mean(sentiment_scores))
        std_sentiment = float(np.std(sentiment_scores))

        if std_sentiment == 0:
            return AlphaSignal(direction="neutral", strength=0.0, confidence=0.0, expected_horizon="short")

        z_score = mean_sentiment / std_sentiment

        if z_score > 1.0:
            direction = "bullish"
        elif z_score < -1.0:
            direction = "bearish"
        else:
            direction = "neutral"

        strength = min(abs(z_score) / 3.0, 1.0)
        confidence = min(abs(z_score) / 2.0, 1.0)

        return AlphaSignal(
            direction=direction,
            strength=strength,
            confidence=confidence,
            expected_horizon="short",
        )

    def onchain_alpha(self, exchange_flows: np.ndarray) -> AlphaSignal:
        if len(exchange_flows) < 2 or exchange_flows.shape[1] != 2:
            return AlphaSignal(direction="neutral", strength=0.0, confidence=0.0, expected_horizon="short")

        inflows = exchange_flows[:, 0]
        outflows = exchange_flows[:, 1]

        net_flow = np.mean(outflows - inflows)
        correlation = np.corrcoef(inflows, outflows)[0, 1] if len(inflows) > 1 else 0.0

        if np.isnan(correlation):
            correlation = 0.0

        if net_flow > 0:
            direction = "bullish"
        elif net_flow < 0:
            direction = "bearish"
        else:
            direction = "neutral"

        strength = min(abs(net_flow) / (np.std(exchange_flows) + 1e-9), 1.0)
        confidence = min(abs(correlation), 1.0)

        return AlphaSignal(
            direction=direction,
            strength=strength,
            confidence=confidence,
            expected_horizon="short",
        )
