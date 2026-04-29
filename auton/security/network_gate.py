"""Network access control for Project ÆON."""

from __future__ import annotations

import asyncio
import fnmatch
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import httpx

from .exceptions import NetworkBlocked
from .config import NetworkRule


@dataclass(frozen=True)
class NetworkLogEntry:
    """Immutable record of a single network request."""

    timestamp: str
    source_module: str
    method: str
    url: str
    status_code: int | None
    request_size: int
    response_size: int
    duration_ms: float
    allowed: bool
    block_reason: str | None = None


class _TokenBucket:
    """Simple in-memory token bucket for rate limiting."""

    def __init__(self, max_requests: int, max_bytes: int, window_seconds: float = 60.0) -> None:
        self._max_requests = max_requests
        self._max_bytes = max_bytes
        self._window = window_seconds
        self._lock = asyncio.Lock()
        self._tokens = float(max_requests)
        self._bytes_left = float(max_bytes)
        self._last_update = time.monotonic()

    async def consume(self, request_size: int, response_size: int) -> bool:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._last_update = now

            self._tokens = min(self._max_requests, self._tokens + elapsed * (self._max_requests / self._window))
            self._bytes_left = min(self._max_bytes, self._bytes_left + elapsed * (self._max_bytes / self._window))

            if self._tokens < 1 or self._bytes_left < (request_size + response_size):
                return False

            self._tokens -= 1
            self._bytes_left -= (request_size + response_size)
            return True

    def status(self) -> dict[str, float]:
        return {
            "requests_remaining": self._tokens,
            "bytes_remaining": self._bytes_left,
            "window_seconds": self._window,
        }


class NetworkGate:
    """Intercept, filter, rate-limit, and log every outbound byte.

    No module may open a raw socket or use ``requests``/``httpx`` directly;
    all traffic goes through the gate.
    """

    def __init__(
        self,
        audit_log=None,
        proxy_url: str | None = None,
        rules: Sequence[NetworkRule] | None = None,
        blocklist_ips: Sequence[str] | None = None,
        require_https: bool = True,
    ) -> None:
        self._audit_log = audit_log
        self._proxy_url = proxy_url
        self._rules: list[NetworkRule] = list(rules) if rules else []
        self._blocklist_ips: set[str] = set(blocklist_ips) if blocklist_ips else set()
        self._require_https = require_https
        self._buckets: dict[str, _TokenBucket] = {}
        self._client: httpx.AsyncClient | None = None

    def add_rule(self, rule: NetworkRule) -> None:
        """Append or replace a rule by domain."""
        self._rules = [r for r in self._rules if r.domain != rule.domain]
        self._rules.append(rule)
        self._buckets.pop(rule.domain, None)

    def remove_rule(self, domain: str) -> None:
        """Remove the rule for *domain*."""
        self._rules = [r for r in self._rules if r.domain != domain]
        self._buckets.pop(domain, None)

    def add_blocklist_ip(self, ip: str) -> None:
        """Add an IP or CIDR range to the blocklist."""
        self._blocklist_ips.add(ip)

    def remove_blocklist_ip(self, ip: str) -> None:
        """Remove an IP or CIDR range from the blocklist."""
        self._blocklist_ips.discard(ip)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            mounts: dict[str, httpx.AsyncHTTPTransport] | None = None
            if self._proxy_url:
                mounts = {
                    "all://": httpx.AsyncHTTPTransport(proxy=self._proxy_url),
                }
            self._client = httpx.AsyncClient(
                mounts=mounts,
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self._client

    def _match_rule(self, url: str) -> NetworkRule | None:
        """Return the first matching rule or None."""
        parsed = httpx.URL(url)
        host = parsed.host
        for rule in self._rules:
            if fnmatch.fnmatch(host, rule.domain):
                return rule
        return None

    def _default_action(self, url: str) -> str:
        """Default deny unless there is an explicit allow rule."""
        return "deny"

    def _is_ip_blocked(self, url: str) -> bool:
        """Check if the resolved IP of the URL is in the blocklist."""
        if not self._blocklist_ips:
            return False
        parsed = httpx.URL(url)
        host = parsed.host
        try:
            addr = ipaddress.ip_address(host)
            for blocked in self._blocklist_ips:
                try:
                    if addr in ipaddress.ip_network(blocked, strict=False):
                        return True
                except ValueError:
                    if str(addr) == blocked:
                        return True
            return False
        except ValueError:
            for blocked in self._blocklist_ips:
                if host == blocked:
                    return True
        return False

    def _check_https(self, url: str) -> bool:
        """Return True if the URL uses HTTPS or if HTTPS is not required."""
        if not self._require_https:
            return True
        parsed = httpx.URL(url)
        return parsed.scheme == "https"

    async def request(
        self,
        method: str,
        url: str,
        *,
        source_module: str,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, bytes, Mapping[str, str]]:
        """Execute an HTTP request through the gate.

        :returns: (status_code, response_body, response_headers)
        :raises NetworkBlocked: If the request violates policy.
        """
        import datetime

        # HTTPS enforcement
        if not self._check_https(url):
            entry = NetworkLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_module=source_module,
                method=method,
                url=url,
                status_code=None,
                request_size=len(body) if body else 0,
                response_size=0,
                duration_ms=0.0,
                allowed=False,
                block_reason="https_required",
            )
            if self._audit_log:
                self._audit_log.log("network_blocked", {"entry": entry.__dict__})
            raise NetworkBlocked(f"HTTPS required: {method} {url}")

        # IP blocklist
        if self._is_ip_blocked(url):
            entry = NetworkLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_module=source_module,
                method=method,
                url=url,
                status_code=None,
                request_size=len(body) if body else 0,
                response_size=0,
                duration_ms=0.0,
                allowed=False,
                block_reason="blocklist",
            )
            if self._audit_log:
                self._audit_log.log("network_blocked", {"entry": entry.__dict__})
            raise NetworkBlocked(f"Network request blocked (IP blocklist): {method} {url}")

        # Domain rule matching
        rule = self._match_rule(url)
        action = rule.action if rule else self._default_action(url)

        if action == "deny":
            entry = NetworkLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_module=source_module,
                method=method,
                url=url,
                status_code=None,
                request_size=len(body) if body else 0,
                response_size=0,
                duration_ms=0.0,
                allowed=False,
                block_reason="domain_policy",
            )
            if self._audit_log:
                self._audit_log.log("network_blocked", {"entry": entry.__dict__})
            raise NetworkBlocked(f"Network request blocked: {method} {url}")

        # Rate limit check
        domain = rule.domain if rule else httpx.URL(url).host
        bucket = self._buckets.get(domain)
        if bucket is None and rule:
            bucket = _TokenBucket(rule.max_requests_per_minute, rule.max_bytes_per_minute)
            self._buckets[domain] = bucket

        if bucket:
            allowed = await bucket.consume(len(body) if body else 0, 0)
            if not allowed:
                entry = NetworkLogEntry(
                    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    source_module=source_module,
                    method=method,
                    url=url,
                    status_code=None,
                    request_size=len(body) if body else 0,
                    response_size=0,
                    duration_ms=0.0,
                    allowed=False,
                    block_reason="rate_limit",
                )
                if self._audit_log:
                    self._audit_log.log("network_rate_limited", {"entry": entry.__dict__})
                raise NetworkBlocked(f"Rate limit exceeded for {domain}")

        start = time.perf_counter()
        client = await self._get_client()
        try:
            response = await client.request(
                method=method,
                url=url,
                headers=dict(headers) if headers else None,
                content=body,
                timeout=timeout,
            )
            elapsed = (time.perf_counter() - start) * 1000
            entry = NetworkLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_module=source_module,
                method=method,
                url=url,
                status_code=response.status_code,
                request_size=len(body) if body else 0,
                response_size=len(response.content),
                duration_ms=elapsed,
                allowed=True,
                block_reason=None,
            )
            if self._audit_log:
                self._audit_log.log("network_request", {"entry": entry.__dict__})
            return response.status_code, response.content, dict(response.headers)
        except httpx.HTTPStatusError as exc:
            elapsed = (time.perf_counter() - start) * 1000
            entry = NetworkLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_module=source_module,
                method=method,
                url=url,
                status_code=exc.response.status_code,
                request_size=len(body) if body else 0,
                response_size=0,
                duration_ms=elapsed,
                allowed=True,
                block_reason=None,
            )
            if self._audit_log:
                self._audit_log.log("network_request", {"entry": entry.__dict__})
            raise
        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            entry = NetworkLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                source_module=source_module,
                method=method,
                url=url,
                status_code=None,
                request_size=len(body) if body else 0,
                response_size=0,
                duration_ms=elapsed,
                allowed=True,
                block_reason=None,
            )
            if self._audit_log:
                self._audit_log.log("network_error", {"entry": entry.__dict__, "error": str(exc)})
            raise

    def get_rate_limit_status(self, domain: str) -> dict[str, Any]:
        """Return current rate limiter state for *domain*."""
        bucket = self._buckets.get(domain)
        if bucket is None:
            rule = self._match_rule(f"https://{domain}")
            if rule:
                return {
                    "requests_remaining": float(rule.max_requests_per_minute),
                    "bytes_remaining": float(rule.max_bytes_per_minute),
                    "window_seconds": 60.0,
                }
            return {"requests_remaining": None, "bytes_remaining": None, "window_seconds": 60.0}
        return bucket.status()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
