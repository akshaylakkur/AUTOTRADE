"""Live simulation runner for Project ÆON.

Fetches real market data from Binance (public API, no auth needed),
performs lightweight web research for sentiment/context, evaluates
trading opportunities via the decision engine, and simulates trades
with fake money.

Usage:
    python simulate_live.py [--symbol BTCUSDT] [--iterations 10] [--interval 1m]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# ÆON imports
# ---------------------------------------------------------------------------
from auton.cortex.decision_engine import (
    Opportunity,
    OpportunityEvaluator,
    OpportunityScore,
    RiskAssessment,
    RiskEngine,
)
from auton.cortex.expansionism import CapitalAllocator
from auton.simulation.session import SimulationSession, SimulationConfig
from auton.simulation.analyzer import SimulationAnalyzer, SimulationMetrics
from auton.simulation.clock import SimulationClock
from auton.simulation.connectors.cost_estimator import CostEstimator
from auton.simulation.recorder import RecordedEvent
from auton.simulation.wallet import SimulatedWallet
from auton.senses.market_data.binance_spot import BinanceSpotConnector
from auton.senses.dataclasses import Candle

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("simulate_live")


# ---------------------------------------------------------------------------
# Web research (lightweight — no auth needed)
# ---------------------------------------------------------------------------
@dataclass
class WebInsight:
    """A single insight gathered from the open web."""

    source: str
    topic: str
    sentiment: str  # "bullish", "bearish", "neutral"
    confidence: float  # 0.0 - 1.0
    raw: dict[str, Any] = field(default_factory=dict)


class WebResearchSimulator:
    """Fetches real public data for sentiment/context.

    Uses free, no-auth endpoints:
    - CoinGecko global crypto data (market cap, dominance)
    - Fear & Greed index (alternative.me)
    """

    def __init__(self, clock: SimulationClock | None = None) -> None:
        self._clock = clock
        self._client: Any | None = None

    async def _get_client(self) -> Any:
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def fetch_fear_greed(self) -> WebInsight:
        """Fetch the Crypto Fear & Greed Index."""
        client = await self._get_client()
        try:
            resp = await client.get("https://api.alternative.me/fng/")
            data = resp.json()["data"][0]
            value = int(data["value"])
            classification = data["value_classification"].lower()
            if "extreme fear" in classification or "fear" in classification:
                sentiment = "bearish"
                conf = min(1.0, (50 - value) / 50 + 0.3)
            elif "extreme greed" in classification or "greed" in classification:
                sentiment = "bullish"
                conf = min(1.0, (value - 50) / 50 + 0.3)
            else:
                sentiment = "neutral"
                conf = 0.5
            return WebInsight(
                source="alternative.me",
                topic="fear_greed",
                sentiment=sentiment,
                confidence=round(conf, 2),
                raw={"value": value, "classification": classification},
            )
        except Exception as exc:
            logger.warning("Fear/Greed fetch failed: %s", exc)
            return WebInsight(
                source="alternative.me",
                topic="fear_greed",
                sentiment="neutral",
                confidence=0.3,
                raw={"error": str(exc)},
            )

    async def fetch_coingecko_global(self) -> WebInsight:
        """Fetch global crypto market data from CoinGecko."""
        client = await self._get_client()
        try:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/global",
                headers={"Accept": "application/json"},
            )
            data = resp.json()["data"]
            market_change = data["market_cap_change_percentage_24h_usd"]
            if market_change > 2.0:
                sentiment = "bullish"
            elif market_change < -2.0:
                sentiment = "bearish"
            else:
                sentiment = "neutral"
            conf = min(1.0, abs(market_change) / 10 + 0.3)
            return WebInsight(
                source="coingecko",
                topic="global_market",
                sentiment=sentiment,
                confidence=round(conf, 2),
                raw={"market_cap_change_24h": market_change},
            )
        except Exception as exc:
            logger.warning("CoinGecko fetch failed: %s", exc)
            return WebInsight(
                source="coingecko",
                topic="global_market",
                sentiment="neutral",
                confidence=0.3,
                raw={"error": str(exc)},
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Strategy engine
# ---------------------------------------------------------------------------
class MomentumStrategy:
    """Simple momentum-based strategy.

    Looks at recent price action + web sentiment to generate
    opportunities.  This is intentionally simple — the point is
    to exercise the full simulation pipeline with real data.
    """

    def __init__(
        self,
        evaluator: OpportunityEvaluator,
        risk_engine: RiskEngine,
        allocator: CapitalAllocator,
    ) -> None:
        self.evaluator = evaluator
        self.risk_engine = risk_engine
        self.allocator = allocator
        self._history: list[Candle] = []

    def update_history(self, candles: list[Candle]) -> None:
        self._history = candles

    def _momentum_signal(self) -> float:
        """Return a raw momentum score based on recent candles."""
        if len(self._history) < 3:
            return 0.0
        recent = self._history[-5:]
        closes = [c.close for c in recent]
        # Simple linear regression slope proxy
        n = len(closes)
        x_avg = (n - 1) / 2
        y_avg = sum(closes) / n
        num = sum((i - x_avg) * (c - y_avg) for i, c in enumerate(closes))
        denom = sum((i - x_avg) ** 2 for i in range(n))
        slope = num / denom if denom else 0.0
        # Normalise to a 0-1 score based on price magnitude
        return min(1.0, max(-1.0, slope / (y_avg * 0.01))) if y_avg else 0.0

    def generate_opportunity(
        self,
        symbol: str,
        balance: float,
        insights: list[WebInsight],
    ) -> Opportunity | None:
        """Generate a single trading opportunity from current state."""
        momentum = self._momentum_signal()

        # Blend momentum + web sentiment into confidence
        web_sentiment = 0.0
        web_confidence = 0.0
        for ins in insights:
            if ins.sentiment == "bullish":
                web_sentiment += ins.confidence
            elif ins.sentiment == "bearish":
                web_sentiment -= ins.confidence
            web_confidence += ins.confidence
        if web_confidence:
            web_sentiment /= web_confidence

        # Combined signal: momentum + sentiment
        combined = (momentum + web_sentiment) / 2
        if combined <= 0:
            return None  # No bullish signal

        confidence = min(1.0, (combined + 1) / 2)

        # Expected return: rough heuristic based on signal strength
        # In a real system this would come from a model
        expected_return = balance * 0.02 * combined  # 2% of balance * signal

        # Risk score: lower when sentiment is strong and consistent
        risk_score = max(0.1, 0.6 - confidence * 0.3)

        # Capital required: small position for tier-0 survival
        capital_required = min(balance * 0.15, 10.0)  # max 15% or $10

        return Opportunity(
            id=f"{symbol}_{datetime.now(timezone.utc).isoformat()}",
            opportunity_type="trade",
            expected_return=expected_return,
            risk_score=round(risk_score, 4),
            capital_required=round(capital_required, 4),
            time_horizon_hours=1.0,
            confidence=round(confidence, 4),
            metadata={
                "symbol": symbol,
                "momentum": round(momentum, 4),
                "web_sentiment": round(web_sentiment, 4),
            },
        )


# ---------------------------------------------------------------------------
# Main simulation runner
# ---------------------------------------------------------------------------
class LiveSimulationRunner:
    """Orchestrates a live simulation with real data."""

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        kline_limit: int = 20,
        iterations: int = 10,
        tick_seconds: float = 60.0,
    ) -> None:
        self.symbol = symbol.upper()
        self.interval = interval
        self.kline_limit = kline_limit
        self.iterations = iterations
        self.tick = timedelta(seconds=tick_seconds)

        # Simulation session
        self.session = SimulationSession(
            SimulationConfig(
                name=f"live_sim_{symbol.lower()}",
                initial_balance=50.0,
                tick_size=self.tick,
            )
        )

        # Decision engine
        self.evaluator = OpportunityEvaluator(
            weights={"return": 0.35, "risk": 0.30, "capital": 0.20, "time": 0.15},
            min_confidence=0.4,
            max_risk_threshold=0.75,
        )
        self.risk_engine = RiskEngine()
        self.allocator = CapitalAllocator(
            survival_reserve_pct=0.10,
            max_position_pct=0.30,
            min_allocation=1.0,
        )
        self.strategy = MomentumStrategy(self.evaluator, self.risk_engine, self.allocator)

        # Data sources
        self.market = BinanceSpotConnector()
        self.web = WebResearchSimulator(clock=self.session.clock)
        self.cost_estimator = CostEstimator(clock=self.session.clock)

        # Caches (avoid hammering APIs)
        self._cached_candles: list[Candle] | None = None
        self._candles_fetched_at: datetime | None = None
        self._cached_insights: list[WebInsight] | None = None
        self._insights_fetched_at: datetime | None = None

    async def run(self) -> SimulationMetrics:
        """Run the full simulation loop."""
        logger.info("=" * 60)
        logger.info("ÆON Live Simulation Starting")
        logger.info("Symbol: %s | Interval: %s | Iterations: %d", self.symbol, self.interval, self.iterations)
        logger.info("Seed balance: $%.2f", self.session.config.initial_balance)
        logger.info("=" * 60)

        self.session.start()

        try:
            await self.market.connect()

            for i in range(self.iterations):
                if i > 0:
                    await asyncio.sleep(2)  # rate-limit politeness
                logger.info("")
                logger.info("--- Iteration %d / %d ---", i + 1, self.iterations)
                await self._step()

        finally:
            await self.market.disconnect()
            await self.web.close()
            self.session.stop()

        return self.session.analyze()

    async def _fetch_candles_coingecko(self) -> list[Candle]:
        """Fetch OHLC data from CoinGecko as a fallback for Binance."""
        import httpx
        symbol_map = {
            "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana",
            "BNBUSDT": "binancecoin", "XRPUSDT": "ripple", "ADAUSDT": "cardano",
            "DOGEUSDT": "dogecoin",
        }
        cg_id = symbol_map.get(self.symbol, "bitcoin")
        days_map = {"1m": 1, "5m": 1, "15m": 1, "1h": 7, "4h": 30, "1d": 90}
        days = days_map.get(self.interval, 1)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc",
                    params={"vs_currency": "usd", "days": days},
                )
                resp.raise_for_status()
                raw = resp.json()
        except Exception as exc:
            logger.warning("CoinGecko OHLC fetch failed: %s", exc)
            return []

        candles: list[Candle] = []
        for item in raw:
            ts_ms = item[0]
            candles.append(
                Candle(
                    source="coingecko",
                    symbol=self.symbol,
                    interval=self.interval,
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=0.0,
                    timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                    metadata={"days": days},
                )
            )
        return candles[-self.kline_limit :] if len(candles) > self.kline_limit else candles

    async def _step(self) -> None:
        """A single simulation step: fetch data → evaluate → trade."""
        now = self.session.advance()
        balance = self.session.wallet.get_balance()

        logger.info("Simulated time: %s | Balance: $%.2f", now.isoformat(), balance)

        # 1. Fetch real market data (Binance primary, CoinGecko fallback, cache)
        candles: list[Candle] = []
        if self._cached_candles is not None:
            candles = self._cached_candles
            logger.info("Using %d cached candles", len(candles))
        else:
            try:
                candles = await self.market.get_klines(
                    self.symbol, self.interval, limit=self.kline_limit
                )
                logger.info("Fetched %d candles from Binance", len(candles))
            except Exception as exc:
                logger.warning("Binance failed (%s), trying CoinGecko fallback...", exc)
                candles = await self._fetch_candles_coingecko()
                if candles:
                    logger.info("Fetched %d candles from CoinGecko", len(candles))
            if candles:
                self._cached_candles = candles
                self._candles_fetched_at = datetime.now(timezone.utc)

        if candles:
            self.strategy.update_history(candles)
            latest = candles[-1]
            self.session.record(
                "market_data",
                "candle",
                {
                    "symbol": self.symbol,
                    "interval": self.interval,
                    "open": latest.open,
                    "high": latest.high,
                    "low": latest.low,
                    "close": latest.close,
                    "volume": latest.volume,
                },
            )
        else:
            logger.error("No market data available this step.")

        # 2. Fetch web insights (with caching)
        insights: list[WebInsight] = []
        if self._cached_insights is not None:
            insights = self._cached_insights
            logger.info("Using %d cached web insights", len(insights))
        else:
            try:
                fg = await self.web.fetch_fear_greed()
                insights.append(fg)
                logger.info("Fear & Greed: %s (confidence: %.2f)", fg.sentiment.upper(), fg.confidence)
            except Exception as exc:
                logger.warning("Web insight fetch failed: %s", exc)

            try:
                cg = await self.web.fetch_coingecko_global()
                insights.append(cg)
                logger.info("Global market: %s (confidence: %.2f)", cg.sentiment.upper(), cg.confidence)
            except Exception as exc:
                logger.warning("CoinGecko fetch failed: %s", exc)

            if insights:
                self._cached_insights = insights
                self._insights_fetched_at = datetime.now(timezone.utc)

        self.session.record(
            "research",
            "web_insights",
            {"insights": [{"source": i.source, "sentiment": i.sentiment, "conf": i.confidence} for i in insights]},
        )

        # 3. Generate opportunity
        opp = self.strategy.generate_opportunity(self.symbol, balance, insights)
        if opp is None:
            logger.info("No bullish opportunity this step. Holding.")
            return

        logger.info(
            "Opportunity: expected_return=$%.2f, risk=%.2f, capital=$%.2f, confidence=%.2f",
            opp.expected_return, opp.risk_score, opp.capital_required, opp.confidence,
        )

        # 4. Evaluate opportunity
        score = self.evaluator.evaluate(opp, balance=balance)
        logger.info(
            "Evaluation: total_score=%.3f | approved=%s | return=%.3f | risk=%.3f | capital=%.3f | time=%.3f",
            score.total_score,
            score.approved,
            score.return_score,
            score.risk_score,
            score.capital_score,
            score.time_score,
        )
        self.session.record(
            "decision",
            "opportunity_evaluated",
            {
                "opportunity_id": opp.id,
                "approved": score.approved,
                "total_score": score.total_score,
                "sub_scores": {
                    "return": score.return_score,
                    "risk": score.risk_score,
                    "capital": score.capital_score,
                    "time": score.time_score,
                },
            },
        )

        if not score.approved:
            logger.info("Opportunity REJECTED by evaluator. Holding.")
            return

        # 5. Risk assessment
        risk = self.risk_engine.assess(opp, balance=balance)
        logger.info(
            "Risk assessment: overall=%.3f | drawdown=%.2f | concentration=%.2f | liquidity=%.2f | tail=%.2f | breach=%s",
            risk.overall_risk,
            risk.max_drawdown_estimate,
            risk.concentration_risk,
            risk.liquidity_risk,
            risk.tail_risk,
            risk.tier_limit_breach,
        )
        self.session.record(
            "risk",
            "assessment",
            {
                "opportunity_id": opp.id,
                "overall_risk": risk.overall_risk,
                "tier_breach": risk.tier_limit_breach,
            },
        )

        if risk.overall_risk > 0.7 or risk.tier_limit_breach:
            logger.info("Opportunity REJECTED by risk engine (too risky). Holding.")
            return

        # 6. Capital allocation
        allocations = self.allocator.allocate(
            balance,
            opportunities=[
                {
                    "id": opp.id,
                    "score": score.total_score,
                    "risk_score": opp.risk_score,
                    "max_allocation": opp.capital_required,
                }
            ],
        )
        if not allocations:
            logger.info("No allocation produced. Holding.")
            return

        alloc = allocations[0]
        logger.info("Allocated: $%.2f to %s", alloc.amount, alloc.opportunity_id)

        # 7. Simulate trade
        if alloc.amount > 0 and balance >= alloc.amount:
            self.session.wallet.debit(alloc.amount, f"position:{self.symbol}")
            # Simulate a return based on signal strength
            # In real life this would depend on actual price movement
            simulated_return = alloc.amount * (opp.expected_return / opp.capital_required) * 0.5
            if simulated_return > 0:
                self.session.wallet.credit(alloc.amount + simulated_return, f"close:{self.symbol}")
                self.session.analyzer.add_return(simulated_return)
                self.session.record(
                    "trade",
                    "executed",
                    {
                        "symbol": self.symbol,
                        "allocated": alloc.amount,
                        "return": round(simulated_return, 4),
                        "net": round(alloc.amount + simulated_return, 4),
                    },
                )
                logger.info(
                    "TRADE EXECUTED: allocated $%.2f, returned $%.2f (net: $%.2f)",
                    alloc.amount,
                    simulated_return,
                    alloc.amount + simulated_return,
                )
            else:
                # Small loss
                loss = alloc.amount * 0.01
                self.session.wallet.credit(alloc.amount - loss, f"close:{self.symbol}")
                self.session.analyzer.add_return(-loss)
                self.session.record(
                    "trade",
                    "executed",
                    {"symbol": self.symbol, "allocated": alloc.amount, "return": round(-loss, 4), "net": round(alloc.amount - loss, 4)},
                )
                logger.info("TRADE EXECUTED: allocated $%.2f, loss $%.2f", alloc.amount, loss)
        else:
            logger.info("Insufficient balance for trade. Holding.")

    def print_report(self, metrics: SimulationMetrics) -> None:
        """Print a formatted simulation report."""
        print("\n" + "=" * 60)
        print("         ÆON LIVE SIMULATION REPORT")
        print("=" * 60)
        print(f"  Session name:     {self.session.config.name}")
        print(f"  Symbol:           {self.symbol}")
        print(f"  Iterations:       {self.iterations}")
        print(f"  Seed balance:     ${self.session.config.initial_balance:.2f}")
        print(f"  Final balance:    ${self.session.wallet.get_balance():.2f}")
        print(f"  Transactions:     {self.session.wallet.get_transaction_count()}")
        print("-" * 60)
        print("  PERFORMANCE METRICS")
        print("-" * 60)
        print(f"  Total P&L:        ${metrics.total_pnl:+.2f}")
        print(f"  Total return:     {metrics.total_return_pct:+.2f}%")
        print(f"  Win rate:         {metrics.win_rate*100:.1f}%")
        print(f"  Wins / Losses:    {metrics.win_count} / {metrics.loss_count}")
        print(f"  Total trades:     {metrics.total_trades}")
        print(f"  Avg trade P&L:    ${metrics.avg_trade_pnl:+.2f}")
        print(f"  Sharpe ratio:     {metrics.sharpe_ratio:.3f}")
        print(f"  Max drawdown:     ${metrics.max_drawdown:.2f} ({metrics.max_drawdown_pct:.1f}%)")
        print("=" * 60)

        # Event breakdown
        events = list(self.session.recorder.get_events())
        print(f"\n  EVENT LOG ({len(events)} events)")
        print("-" * 60)
        for ev in events[-20:]:  # last 20
            print(f"  {ev.timestamp.isoformat()} | {ev.category:12s} | {ev.action:20s}")
        if len(events) > 20:
            print(f"  ... and {len(events) - 20} more events")
        print("=" * 60)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="ÆON Live Simulation Runner")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    parser.add_argument("--interval", default="1m", help="Kline interval (default: 1m)")
    parser.add_argument("--iterations", type=int, default=10, help="Number of simulation steps (default: 10)")
    parser.add_argument("--kline-limit", type=int, default=20, help="Candles per fetch (default: 20)")
    parser.add_argument("--tick-seconds", type=float, default=60.0, help="Simulated tick size in seconds (default: 60)")
    parser.add_argument("--demo", action="store_true", help="Force bullish sentiment for demo purposes")
    parser.add_argument("--min-confidence", type=float, default=0.4, help="Minimum confidence threshold (default: 0.4)")
    args = parser.parse_args()

    runner = LiveSimulationRunner(
        symbol=args.symbol,
        interval=args.interval,
        kline_limit=args.kline_limit,
        iterations=args.iterations,
        tick_seconds=args.tick_seconds,
    )

    # Demo mode: override all gates so trades execute
    if args.demo:
        class DemoEvaluator(OpportunityEvaluator):
            def evaluate(self, opportunity, balance, tier=None):
                score = super().evaluate(opportunity, balance, tier)
                return OpportunityScore(
                    opportunity_id=score.opportunity_id,
                    total_score=score.total_score,
                    return_score=score.return_score,
                    risk_score=score.risk_score,
                    capital_score=score.capital_score,
                    time_score=score.time_score,
                    approved=True,
                    metadata={**score.metadata, "demo": True},
                )

        class DemoRiskEngine(RiskEngine):
            def assess(self, opportunity, balance, tier=None):
                return RiskAssessment(
                    overall_risk=0.05,
                    max_drawdown_estimate=0.01,
                    liquidity_risk=0.01,
                    concentration_risk=0.01,
                    tail_risk=0.02,
                    tier_limit_breach=False,
                    metadata={"demo": True},
                )
            def within_limits(self, opportunity, balance, tier=None):
                return True

        runner.evaluator = DemoEvaluator(
            weights={"return": 0.35, "risk": 0.30, "capital": 0.20, "time": 0.15},
            min_confidence=0.0,
            max_risk_threshold=1.0,
        )
        runner.risk_engine = DemoRiskEngine()

        original_generate = runner.strategy.generate_opportunity
        def demo_generate(symbol, balance, insights):
            opp = original_generate(symbol, balance, insights)
            if opp is None:
                return Opportunity(
                    id=f"demo_{symbol}_{datetime.now(timezone.utc).isoformat()}",
                    opportunity_type="trade",
                    expected_return=balance * 0.05,
                    risk_score=0.3,
                    capital_required=5.0,
                    time_horizon_hours=1.0,
                    confidence=0.7,
                    metadata={"symbol": symbol, "demo": True},
                )
            return opp
        runner.strategy.generate_opportunity = demo_generate
        logger.info("DEMO MODE: All gates bypassed")

    metrics = asyncio.run(runner.run())
    runner.print_report(metrics)


if __name__ == "__main__":
    main()
