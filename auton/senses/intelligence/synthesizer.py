"""Combine multiple sources into coherent briefings with confidence scores."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from auton.senses.intelligence.scraper import ScrapedContent

logger = logging.getLogger(__name__)

# Domain reputation tiers: higher is more credible
_DOMAIN_REPUTATION: dict[str, float] = {
    "arxiv.org": 0.95,
    "github.com": 0.90,
    "stackoverflow.com": 0.88,
    "news.ycombinator.com": 0.75,
    "medium.com": 0.65,
    "dev.to": 0.70,
    "reddit.com": 0.55,
    "twitter.com": 0.45,
    "x.com": 0.45,
    "facebook.com": 0.40,
    "tiktok.com": 0.30,
    "pinterest.com": 0.35,
}

_DEFAULT_DOMAIN_REP = 0.60


@dataclass(frozen=True)
class SourceBrief:
    """A briefed version of a single source."""

    url: str
    title: str
    summary: str
    credibility: float  # 0.0 to 1.0
    word_count: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SynthesisReport:
    """Combined briefing from multiple sources."""

    query: str
    briefs: list[SourceBrief]
    overall_confidence: float
    top_insights: list[str]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ResearchSynthesizer:
    """Synthesize scraped content into structured briefings.

    Uses domain-reputation heuristics, keyword-density scoring, and
    simple redundancy detection to produce a ranked summary.
    """

    def __init__(
        self,
        max_summary_words: int = 150,
        min_credibility: float = 0.20,
    ) -> None:
        self._max_summary_words = max_summary_words
        self._min_credibility = min_credibility

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize(
        self,
        query: str,
        contents: list[ScrapedContent],
    ) -> SynthesisReport:
        """Turn scraped pages into a structured report.

        Args:
            query: The original research query.
            contents: Scraped pages (may contain ``None`` entries — they are ignored).

        Returns:
            A ``SynthesisReport`` with ranked briefs and overall confidence.
        """
        valid = [c for c in contents if c is not None and c.word_count >= 10]
        briefs: list[SourceBrief] = []
        for content in valid:
            credibility = self._score_credibility(content)
            if credibility < self._min_credibility:
                continue
            summary = self._summarize(content)
            briefs.append(
                SourceBrief(
                    url=content.url,
                    title=content.title,
                    summary=summary,
                    credibility=credibility,
                    word_count=content.word_count,
                )
            )

        # Sort by credibility descending
        briefs.sort(key=lambda b: b.credibility, reverse=True)
        overall_confidence = self._compute_overall_confidence(briefs)
        top_insights = self._extract_insights(query, briefs)

        return SynthesisReport(
            query=query,
            briefs=briefs,
            overall_confidence=overall_confidence,
            top_insights=top_insights,
        )

    def score_opportunity(
        self,
        report: SynthesisReport,
        opportunity_keywords: list[str] | None = None,
    ) -> float:
        """Score how strongly the report signals an actionable opportunity.

        Returns a value between 0.0 and 1.0.
        """
        if not report.briefs:
            return 0.0

        keywords = opportunity_keywords or [
            "profit",
            "revenue",
            "arbitrage",
            "opportunity",
            "demand",
            "shortage",
            "trend",
            "growth",
            "freelance",
            "gig",
            "saas",
            "api",
        ]
        keyword_pattern = re.compile(
            r"\b(" + "|".join(re.escape(kw) for kw in keywords) + r")\b",
            re.IGNORECASE,
        )

        total_hits = 0
        total_words = 0
        for brief in report.briefs:
            text = f"{brief.title} {brief.summary}"
            hits = len(keyword_pattern.findall(text))
            total_hits += hits
            total_words += len(text.split())

        if total_words == 0:
            return 0.0

        density = total_hits / (total_words + 1)
        # Scale so that ~5% keyword density = ~0.8 score
        raw_score = density * 20
        return min(raw_score * report.overall_confidence, 1.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _score_credibility(self, content: ScrapedContent) -> float:
        """Compute credibility based on domain reputation and content heuristics."""
        from urllib.parse import urlparse

        domain = urlparse(content.url).netloc.lower()
        # Strip www. prefix for lookup
        if domain.startswith("www."):
            domain = domain[4:]

        base = _DOMAIN_REPUTATION.get(domain, _DEFAULT_DOMAIN_REP)

        # Boost for length (more substantive content)
        length_boost = min(content.word_count / 500, 0.10)

        # Penalty for very short content
        penalty = 0.0 if content.word_count >= 50 else 0.30

        return max(0.0, min(1.0, base + length_boost - penalty))

    def _summarize(self, content: ScrapedContent) -> str:
        """Extract a truncated summary from content text."""
        words = content.text.split()
        if len(words) <= self._max_summary_words:
            return content.text
        # Take first N words and add ellipsis
        return " ".join(words[: self._max_summary_words]) + "..."

    @staticmethod
    def _compute_overall_confidence(briefs: list[SourceBrief]) -> float:
        if not briefs:
            return 0.0
        # Weighted average by credibility
        total_weight = sum(b.credibility for b in briefs)
        if total_weight == 0:
            return 0.0
        weighted = sum(b.credibility * b.credibility for b in briefs) / total_weight
        # Boost with more sources, up to a point
        source_boost = min(len(briefs) * 0.05, 0.15)
        return min(1.0, weighted + source_boost)

    def _extract_insights(self, query: str, briefs: list[SourceBrief]) -> list[str]:
        """Generate top-level insight sentences from briefs."""
        insights: list[str] = []
        for brief in briefs[:3]:
            sentence = f"[{brief.title}] {brief.summary[:120]}..."
            insights.append(sentence)
        return insights
