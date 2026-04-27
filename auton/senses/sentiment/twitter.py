"""Twitter/X sentiment connector (skeleton implementation).

This is a skeleton connector that simulates tweet search and performs
basic keyword-based sentiment scoring without calling the real X API.
Tier 2+ is required to "unlock" the simulated data feed.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from auton.senses.base_connector import BaseConnector
from auton.senses.dataclasses import SentimentScore


class TwitterSentimentConnector(BaseConnector):
    """Skeleton Twitter/X sentiment connector.

    Uses a simple keyword-based sentiment scorer (VADER-like heuristic)
    and simulates tweet search. No real API credentials are required.
    The connector is gated behind tier 2+ to simulate a paid feed.
    """

    POSITIVE_WORDS = frozenset(
        [
            "good",
            "great",
            "excellent",
            "amazing",
            "awesome",
            "bullish",
            "moon",
            "pump",
            "profit",
            "gain",
            "rise",
            "up",
            "surge",
            "breakout",
            "strong",
            "buy",
            "hodl",
            "win",
        ]
    )

    NEGATIVE_WORDS = frozenset(
        [
            "bad",
            "terrible",
            "awful",
            "bearish",
            "dump",
            "crash",
            "loss",
            "lose",
            "down",
            "drop",
            "fall",
            "weak",
            "sell",
            "panic",
            "fear",
            "scam",
            "rug",
        ]
    )

    def __init__(
        self,
        event_bus: Any | None = None,
    ) -> None:
        super().__init__(event_bus=event_bus)
        self._simulated_tweets: list[dict[str, Any]] = []

    async def connect(self) -> None:
        async with self._lock:
            self._connected = True

    async def disconnect(self) -> None:
        async with self._lock:
            self._connected = False

    async def fetch_data(self, params: dict[str, Any]) -> dict[str, Any]:
        """Simulated fetch returning mock tweet payloads."""
        query = params.get("query", "")
        max_results = params.get("max_results", 10)
        tweets = await self.search_tweets(query, max_results)
        result = {"tweets": [t.__dict__ for t in tweets], "query": query}
        await self._emit_data(result)
        return result

    async def search_tweets(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[SentimentScore]:
        """Return simulated tweet sentiment scores for a query.

        This is a skeleton method — it generates mock data rather than
        calling the real X API.
        """
        await asyncio.sleep(0)  # yield control in case we ever do real I/O
        scores: list[SentimentScore] = []
        for i in range(max_results):
            text = f"Mock tweet {i} about {query}"
            score = self._keyword_score(text)
            scores.append(
                SentimentScore(
                    source="twitter_skeleton",
                    query=query,
                    score=score,
                    magnitude=abs(score),
                    timestamp=datetime.now(timezone.utc),
                    metadata={"mock": True, "index": i, "text": text},
                )
            )
        return scores

    def get_sentiment_score(self, texts: list[str]) -> SentimentScore:
        """Score a batch of texts and return the average sentiment.

        Args:
            texts: List of raw text strings.

        Returns:
            A ``SentimentScore`` with the mean score and mean magnitude.
        """
        if not texts:
            return SentimentScore(
                source="twitter_skeleton",
                query="batch",
                score=0.0,
                magnitude=0.0,
            )
        scores = [self._keyword_score(t) for t in texts]
        avg_score = sum(scores) / len(scores)
        avg_magnitude = sum(abs(s) for s in scores) / len(scores)
        return SentimentScore(
            source="twitter_skeleton",
            query="batch",
            score=avg_score,
            magnitude=avg_magnitude,
        )

    def _keyword_score(self, text: str) -> float:
        """Simple keyword-based sentiment heuristic.

        Returns a score between -1.0 (most negative) and +1.0 (most positive).
        """
        words = re.findall(r"[a-zA-Z]+", text.lower())
        pos = sum(1 for w in words if w in self.POSITIVE_WORDS)
        neg = sum(1 for w in words if w in self.NEGATIVE_WORDS)
        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / max(total, 3)

    # ------------------------------------------------------------------
    # Cost & tier
    # ------------------------------------------------------------------

    def get_subscription_cost(self) -> dict[str, float]:
        return {"monthly": 99.0, "daily": 3.3}

    def is_available(self, tier: int) -> bool:
        return tier >= 2
