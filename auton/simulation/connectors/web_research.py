"""WebResearchSimulator — real web searches, tagged as simulated."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from auton.simulation.clock import SimulationClock
from auton.simulation.connectors.dataclasses import SimulatedData
from auton.simulation.recorder import SimulationRecorder


class WebResearchSimulator:
    """Performs real HTTP requests for web research and tags results as simulated.

    The default endpoint queries Wikipedia's REST summary API, but any
    public HTTP endpoint can be injected for testing or alternative
    research sources.

    All responses are wrapped in :class:`SimulatedData` with
    ``simulated=True`` so downstream code knows the data came from
    the sandbox.
    """

    DEFAULT_ENDPOINT = "https://en.wikipedia.org/api/rest_v1/page/summary/"

    def __init__(
        self,
        search_endpoint: str | None = None,
        recorder: SimulationRecorder | None = None,
        clock: SimulationClock | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._search_endpoint = (search_endpoint or self.DEFAULT_ENDPOINT).rstrip("/")
        self._recorder = recorder
        self._clock = clock
        self._client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    # ------------------------------------------------------------------
    # Research helpers
    # ------------------------------------------------------------------

    async def search(self, query: str, max_results: int = 5) -> SimulatedData:
        """Perform a real HTTP search for *query* and return simulated data.

        Args:
            query: The search term (spaces replaced with underscores).
            max_results: Ignored for the Wikipedia endpoint; reserved for
                paginated search APIs.

        Returns:
            A :class:`SimulatedData` wrapping the JSON response or an
            error payload if the request fails.
        """
        safe_query = query.replace(" ", "_")
        url = f"{self._search_endpoint}/{safe_query}"

        try:
            response = await self._client.get(url, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            payload = {"error": str(exc), "query": query, "url": url}

        sim = SimulatedData(
            source="web_research",
            data_type="search_result",
            payload=payload,
            sim_time=self._sim_time(),
            metadata={
                "query": query,
                "safe_query": safe_query,
                "max_results": max_results,
                "endpoint": self._search_endpoint,
                "url": url,
            },
        )
        self._record("research", "web_search", {"simulated_data": sim})
        return sim

    async def multi_search(self, queries: list[str]) -> list[SimulatedData]:
        """Run :meth:`search` for multiple queries concurrently."""
        tasks = [self.search(q) for q in queries]
        return await asyncio.gather(*tasks)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

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
