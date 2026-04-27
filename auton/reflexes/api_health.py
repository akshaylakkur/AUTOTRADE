import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Optional

from auton.core import EventBus
from auton.reflexes.dataclasses import ApiDown, ApiRecovered, HealthStatus

HTTPClient = Callable[[str], Awaitable[int]]


class APIHealthMonitor:
    def __init__(self, event_bus: EventBus, http_client: Optional[HTTPClient] = None) -> None:
        self._event_bus = event_bus
        self._apis: Dict[str, dict] = {}
        self._status: Dict[str, HealthStatus] = {}
        self._http_client = http_client

    def register_api(
        self,
        name: str,
        health_endpoint: str,
        interval_seconds: int,
        failover_endpoint: Optional[str] = None,
    ) -> None:
        self._apis[name] = {
            "endpoint": health_endpoint,
            "interval": interval_seconds,
            "failover": failover_endpoint,
        }
        self._status[name] = HealthStatus(
            name=name,
            endpoint=health_endpoint,
            healthy=True,
            last_checked=datetime.now(timezone.utc),
            latency_ms=0.0,
        )

    async def check_health(self) -> None:
        for name, config in self._apis.items():
            start = asyncio.get_event_loop().time()
            healthy = False
            try:
                if self._http_client is not None:
                    status = await self._http_client(config["endpoint"])
                    healthy = status == 200
                else:
                    healthy = True
            except Exception:
                healthy = False
            latency = (asyncio.get_event_loop().time() - start) * 1000

            prev = self._status[name]
            if healthy and not prev.healthy:
                await self._event_bus.publish(ApiRecovered, ApiRecovered(name=name))
            elif not healthy and prev.healthy:
                await self._event_bus.publish(
                    ApiDown,
                    ApiDown(name=name, endpoint=config["endpoint"]),
                )

            self._status[name] = HealthStatus(
                name=name,
                endpoint=config["endpoint"],
                healthy=healthy,
                last_checked=datetime.now(timezone.utc),
                latency_ms=latency,
            )

    def get_failover(self, name: str) -> Optional[str]:
        status = self._status.get(name)
        if status is None:
            return None
        if not status.healthy:
            return self._apis[name].get("failover")
        return None

    def is_healthy(self, name: str) -> bool:
        status = self._status.get(name)
        return status.healthy if status is not None else False

    def get_status(self, name: str) -> Optional[HealthStatus]:
        return self._status.get(name)
