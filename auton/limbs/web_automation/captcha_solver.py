"""CAPTCHA detection and solving integration for web automation.

Supports 2Captcha / Anti-Captcha style API pattern.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page

from .dataclasses import CaptchaInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptchaSolution:
    """Result from a CAPTCHA solving service."""

    token: str
    cost: float
    service: str
    solve_time_seconds: float


class CaptchaSolver:
    """Detects CAPTCHAs on a page and forwards them to a solving service.

    Parameters
    ----------
    api_key:
        API key for the solving service.
    service:
        One of ``2captcha`` or ``anticaptcha``.
    base_url:
        API base URL for the service.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        service: str = "2captcha",
        base_url: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._service = service.lower()
        self._base_url = base_url or self._default_base_url()

    @staticmethod
    def _default_base_url() -> str:
        return "http://2captcha.com"

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #

    async def detect(self, page: Page) -> CaptchaInfo | None:
        """Scan *page* for known CAPTCHA indicators.

        Returns :class:`CaptchaInfo` if a CAPTCHA is found, else ``None``.
        """
        # reCAPTCHA v2
        recaptcha_frame = await page.query_selector("iframe[src*='recaptcha']")
        if recaptcha_frame:
            src = await recaptcha_frame.get_attribute("src") or ""
            site_key = self._extract_param(src, "k")
            return CaptchaInfo(
                captcha_type="recaptcha_v2",
                site_key=site_key,
                url=page.url,
                iframe_selector="iframe[src*='recaptcha']",
            )

        # reCAPTCHA v3 (invisible badge)
        recaptcha_badge = await page.query_selector(".grecaptcha-badge")
        if recaptcha_badge:
            return CaptchaInfo(
                captcha_type="recaptcha_v3",
                url=page.url,
            )

        # hCaptcha
        hcaptcha_frame = await page.query_selector("iframe[src*='hcaptcha.com']")
        if hcaptcha_frame:
            src = await hcaptcha_frame.get_attribute("src") or ""
            site_key = self._extract_param(src, "sitekey")
            return CaptchaInfo(
                captcha_type="hcaptcha",
                site_key=site_key,
                url=page.url,
                iframe_selector="iframe[src*='hcaptcha.com']",
            )

        # Image CAPTCHA heuristic
        img_captcha = await page.query_selector("img[src*='captcha']")
        if img_captcha:
            img_url = await img_captcha.get_attribute("src")
            return CaptchaInfo(
                captcha_type="image_captcha",
                url=page.url,
                image_url=img_url,
            )

        return None

    # ------------------------------------------------------------------ #
    # Solving
    # ------------------------------------------------------------------ #

    async def solve(self, captcha_info: CaptchaInfo, page: Page | None = None) -> CaptchaSolution:
        """Submit *captcha_info* to the solving service and return the token.

        If no API key is configured, returns a dummy token (test mode).
        """
        if self._api_key is None:
            logger.warning("CaptchaSolver: no API key configured; returning dummy token")
            return CaptchaSolution(
                token="DUMMY_TOKEN",
                cost=0.0,
                service=self._service,
                solve_time_seconds=0.0,
            )

        start = asyncio.get_event_loop().time()

        if captcha_info.captcha_type in ("recaptcha_v2", "recaptcha_v3"):
            token = await self._solve_recaptcha(captcha_info)
        elif captcha_info.captcha_type == "hcaptcha":
            token = await self._solve_hcaptcha(captcha_info)
        elif captcha_info.captcha_type == "image_captcha":
            token = await self._solve_image_captcha(captcha_info, page)
        else:
            raise ValueError(f"Unsupported CAPTCHA type: {captcha_info.captcha_type}")

        elapsed = asyncio.get_event_loop().time() - start
        cost = self._estimate_cost(captcha_info)
        return CaptchaSolution(
            token=token,
            cost=cost,
            service=self._service,
            solve_time_seconds=elapsed,
        )

    async def solve_on_page(self, page: Page) -> CaptchaSolution | None:
        """Convenience: detect + solve on *page*."""
        info = await self.detect(page)
        if info is None:
            return None
        solution = await self.solve(info, page)
        await self._inject_solution(page, info, solution)
        return solution

    # ------------------------------------------------------------------ #
    # Injection
    # ------------------------------------------------------------------ #

    async def _inject_solution(
        self,
        page: Page,
        info: CaptchaInfo,
        solution: CaptchaSolution,
    ) -> None:
        """Inject the solved token into the page so submission can proceed."""
        if info.captcha_type in ("recaptcha_v2", "recaptcha_v3"):
            await page.evaluate(
                f"() => grecaptcha.getResponse = () => {json.dumps(solution.token)}"
            )
            await page.evaluate(
                "() => { if (typeof grecaptcha !== 'undefined') { document.querySelectorAll('[name=\"g-recaptcha-response\"]').forEach(el => el.value = grecaptcha.getResponse()); } }"
            )
        elif info.captcha_type == "hcaptcha":
            await page.evaluate(
                f"() => {{ if (typeof hcaptcha !== 'undefined') hcaptcha.setResponse({json.dumps(solution.token)}); }}"
            )
        logger.info("CaptchaSolver: injected solution for %s", info.captcha_type)

    # ------------------------------------------------------------------ #
    # Service integrations (skeleton — implement with httpx for live mode)
    # ------------------------------------------------------------------ #

    async def _solve_recaptcha(self, captcha_info: CaptchaInfo) -> str:
        # Skeleton: in a live implementation this would POST to the solving service API.
        logger.info("CaptchaSolver: submitted reCAPTCHA for solving")
        await asyncio.sleep(2)  # simulate polling
        return "FAKE_RECAPTCHA_TOKEN"

    async def _solve_hcaptcha(self, captcha_info: CaptchaInfo) -> str:
        logger.info("CaptchaSolver: submitted hCaptcha for solving")
        await asyncio.sleep(2)
        return "FAKE_HCAPTCHA_TOKEN"

    async def _solve_image_captcha(self, captcha_info: CaptchaInfo, page: Page | None) -> str:
        if captcha_info.image_url and page:
            # Download the image and submit as base64
            import httpx  # lazy import

            async with httpx.AsyncClient() as client:
                r = await client.get(captcha_info.image_url)
                r.raise_for_status()
                b64 = base64.b64encode(r.content).decode("ascii")
                logger.info("CaptchaSolver: submitted image CAPTCHA (%d bytes)", len(r.content))
        await asyncio.sleep(2)
        return "FAKE_IMAGE_CAPTCHA_TEXT"

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_param(url: str, key: str) -> str | None:
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        values = qs.get(key)
        return values[0] if values else None

    def _estimate_cost(self, captcha_info: CaptchaInfo) -> float:
        # Approximate solving service costs in USD
        costs: dict[str, float] = {
            "recaptcha_v2": 0.002,
            "recaptcha_v3": 0.002,
            "hcaptcha": 0.002,
            "image_captcha": 0.001,
        }
        return costs.get(captcha_info.captcha_type, 0.002)
