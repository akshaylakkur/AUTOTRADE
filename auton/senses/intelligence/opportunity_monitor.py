"""Continuous opportunity monitoring for ÆON.

Monitors for:
- SaaS arbitrage opportunities
- Trading signals and market inefficiencies
- Freelance gig markets
- Marketplace trends
- New APIs and developer tools
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from auton.core.event_bus import EventBus
from auton.core.events import OpportunityDiscovered
from auton.senses.intelligence.scraper import ScrapedContent, WebScraper
from auton.senses.intelligence.search_engine import SearchEngine, SearchResult
from auton.senses.intelligence.storage import ResearchStore, ResearchTask
from auton.senses.intelligence.synthesizer import ResearchSynthesizer, SynthesisReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonitorConfig:
    """Configuration for the opportunity monitor."""

    enabled_domains: list[str] = field(
        default_factory=lambda: [
            "saas_arbitrage",
            "trading_signals",
            "freelance_gigs",
            "marketplace_trends",
            "new_apis_tools",
        ]
    )
    queries_per_domain: int = 3
    results_per_query: int = 5
    scrape_concurrency: int = 5
    poll_interval_seconds: float = 3600.0
    min_opportunity_score: float = 0.40
    max_daily_spend: float = 1.00


class OpportunityMonitor:
    """Continuously searches, scrapes, synthesizes, and emits opportunities.

    Integrates with the event bus to publish ``OpportunityDiscovered``
    events whenever a credible opportunity crosses the configured score
    threshold.
    """

    _DOMAIN_QUERIES: dict[str, list[str]] = {
        "saas_arbitrage": [
            "SaaS arbitrage opportunities 2025",
            "resell SaaS tools profit margin",
            "white label SaaS pricing gap",
        ],
        "trading_signals": [
            "crypto arbitrage opportunity today",
            "market inefficiency trading signal",
            "exchange price discrepancy alert",
        ],
        "freelance_gigs": [
            "high paying freelance gigs remote",
            "freelance arbitrage Upwork Fiverr",
            "quick turnaround freelance tasks",
        ],
        "marketplace_trends": [
            "trending product niche 2025",
            "underserved marketplace demand",
            "new marketplace seller opportunity",
        ],
        "new_apis_tools": [
            "new API launched 2025",
            "developer tools beta free tier",
            "API arbitrage opportunity",
        ],
    }

    def __init__(
        self,
        event_bus: EventBus | None = None,
        search_engine: SearchEngine | None = None,
        scraper: WebScraper | None = None,
        synthesizer: ResearchSynthesizer | None = None,
        store: ResearchStore | None = None,
        config: MonitorConfig | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._search = search_engine or SearchEngine()
        self._scraper = scraper or WebScraper()
        self._synth = synthesizer or ResearchSynthesizer()
        self._store = store or ResearchStore()
        self._config = config or MonitorConfig()
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._daily_spend = 0.0
        self._spend_reset_time = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the continuous monitor loop."""
        if self._running:
            return
        self._running = True
        await self._search.connect()
        await self._scraper.connect()
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("OpportunityMonitor started.")

    async def stop(self) -> None:
        """Stop the monitor loop and release connections."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._search.disconnect()
        await self._scraper.disconnect()
        logger.info("OpportunityMonitor stopped.")

    # ------------------------------------------------------------------
    # Public single-run API
    # ------------------------------------------------------------------

    async def run_once(self, domains: list[str] | None = None) -> list[OpportunityDiscovered]:
        """Execute one full monitoring cycle.

        Args:
            domains: Optional subset of domains to scan. Defaults to config.

        Returns:
            A list of discovered opportunities (already emitted to the bus).
        """
        domains = domains or self._config.enabled_domains
        discovered: list[OpportunityDiscovered] = []

        for domain in domains:
            if not self._check_spend_budget():
                logger.warning("Daily research spend exhausted. Skipping remaining domains.")
                break

            queries = self._DOMAIN_QUERIES.get(domain, [])
            selected = queries[: self._config.queries_per_domain]
            for query in selected:
                try:
                    opportunities = await self._research_query(domain, query)
                    discovered.extend(opportunities)
                except Exception:
                    logger.exception("Research failed for query: %s", query)

        return discovered

    async def research(
        self,
        query: str,
        budget: float = 0.0,
        deadline: datetime | None = None,
        domain: str = "custom",
    ) -> tuple[SynthesisReport, list[OpportunityDiscovered]]:
        """Run the full async pipeline for a single query.

        Pipeline: search → scrape → summarize → score → store → emit.

        Returns:
            The synthesis report and any discovered opportunities.
        """
        task = ResearchTask(query=query, budget=budget, deadline=deadline)
        task_id = self._store.save_task(task)

        # 1. Search
        results = await self._search.search(query, num_results=self._config.results_per_query)
        if not results:
            report = SynthesisReport(query=query, briefs=[], overall_confidence=0.0, top_insights=[])
            return report, []

        # 2. Scrape
        urls = [r.url for r in results if r.url]
        scraped_map = await self._scraper.scrape_multiple(urls, concurrency=self._config.scrape_concurrency)
        contents: list[ScrapedContent | None] = [scraped_map.get(u) for u in urls]

        # 3. Synthesize
        report = self._synth.synthesize(query, contents)

        # 4. Score opportunity
        opp_score = self._synth.score_opportunity(report)

        # 5. Store
        sources = [
            {
                "url": b.url,
                "title": b.title,
                "credibility": b.credibility,
                "summary": b.summary,
            }
            for b in report.briefs
        ]
        self._store.save_result(
            task_id=task_id,
            query=query,
            summary="; ".join(report.top_insights) if report.top_insights else "",
            confidence=report.overall_confidence,
            opportunity_score=opp_score,
            domain=domain,
            data={
                "brief_count": len(report.briefs),
                "top_insights": report.top_insights,
            },
            sources=sources,
        )

        # 6. Emit if above threshold
        opportunities: list[OpportunityDiscovered] = []
        if opp_score >= self._config.min_opportunity_score:
            opp = OpportunityDiscovered(
                domain=domain,
                description=query,
                estimated_value=self._estimate_value(report, opp_score),
                confidence=min(report.overall_confidence, opp_score),
            )
            opportunities.append(opp)
            if self._event_bus is not None:
                await self._event_bus.publish(OpportunityDiscovered, opp)

        self._track_cost(len(results) * 0.01)  # Nominal cost attribution
        return report, opportunities

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Monitor loop iteration failed.")
            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _research_query(
        self,
        domain: str,
        query: str,
    ) -> list[OpportunityDiscovered]:
        _, opportunities = await self.research(query, domain=domain)
        return opportunities

    def _check_spend_budget(self) -> bool:
        now = datetime.now(timezone.utc)
        if now - self._spend_reset_time > timedelta(days=1):
            self._daily_spend = 0.0
            self._spend_reset_time = now
        return self._daily_spend < self._config.max_daily_spend

    def _track_cost(self, amount: float) -> None:
        self._daily_spend += amount

    @staticmethod
    def _estimate_value(report: SynthesisReport, score: float) -> float:
        """Crude revenue estimate based on keyword density and confidence."""
        base = 50.0  # Minimum speculative value
        multiplier = 1.0 + (len(report.briefs) * 0.5)
        return round(base * multiplier * score, 2)
