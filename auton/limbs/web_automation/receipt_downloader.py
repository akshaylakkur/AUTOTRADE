"""Receipt downloader for web automation.

Downloads receipts, invoices, and confirmations from websites.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Download, Page

from .dataclasses import Receipt

logger = logging.getLogger(__name__)


class ReceiptDownloader:
    """Downloads and parses receipts/invoices from web pages.

    Parameters
    ----------
    download_dir:
        Directory to save downloaded files.
    """

    def __init__(self, download_dir: str | Path = "data/receipts") -> None:
        self._download_dir = Path(download_dir)
        self._download_dir.mkdir(parents=True, exist_ok=True)
        self._pending_downloads: dict[str, Download] = {}

    # ------------------------------------------------------------------ #
    # Download interception
    # ------------------------------------------------------------------ #

    def attach(self, page: Page) -> None:
        """Attach download listener to *page*."""
        page.on("download", self._on_download)

    def detach(self, page: Page) -> None:
        """Detach download listener from *page*."""
        # Playwright does not support removing specific listeners;
        # rely on page closure to clean up.
        pass

    async def _on_download(self, download: Download) -> None:
        suggested = download.suggested_filename
        self._pending_downloads[suggested] = download
        logger.debug("ReceiptDownloader: intercepted download %s", suggested)

    async def wait_for_download(
        self,
        page: Page,
        trigger,
        *,
        timeout_ms: int = 30000,
    ) -> Path:
        """Wait for a download triggered by *trigger*.

        *trigger* is an async callable that initiates the download.
        """
        async with page.expect_download(timeout=timeout_ms) as download_info:
            await trigger()
        download = await download_info.value
        safe_name = f"{uuid.uuid4().hex}_{download.suggested_filename}"
        dest = self._download_dir / safe_name
        await download.save_as(str(dest))
        logger.info("ReceiptDownloader: saved download to %s", dest)
        return dest

    # ------------------------------------------------------------------ #
    # Receipt extraction
    # ------------------------------------------------------------------ #

    async def extract_receipt_from_page(
        self,
        page: Page,
        *,
        merchant: str = "",
        amount_selector: str | None = None,
        id_selector: str | None = None,
        date_selector: str | None = None,
        description_selector: str | None = None,
        currency: str = "USD",
    ) -> Receipt:
        """Parse receipt information from the current page.

        Uses selectors if provided; otherwise falls back to heuristics.
        """
        raw_text = await page.content()

        amount = await self._extract_amount(page, amount_selector, raw_text)
        receipt_id = await self._extract_id(page, id_selector, raw_text)
        date = await self._extract_date(page, date_selector, raw_text)
        description = await self._extract_description(page, description_selector, raw_text)

        if not merchant:
            merchant = await page.title()

        receipt = Receipt(
            receipt_id=receipt_id or str(uuid.uuid4()),
            merchant=merchant,
            amount=amount,
            currency=currency,
            description=description,
            date=date or datetime.now(timezone.utc),
            raw_text=raw_text[:10000],  # truncate for storage
        )
        logger.info("ReceiptDownloader: extracted receipt %s", receipt.receipt_id)
        return receipt

    async def download_and_parse_receipt(
        self,
        page: Page,
        trigger,
        *,
        merchant: str = "",
        currency: str = "USD",
        timeout_ms: int = 30000,
    ) -> tuple[Receipt, Path | None]:
        """Trigger a download, save the file, and parse the page as receipt."""
        pdf_path: Path | None = None
        try:
            pdf_path = await self.wait_for_download(page, trigger, timeout_ms=timeout_ms)
        except Exception:  # noqa: BLE001
            logger.warning("ReceiptDownloader: no download triggered, parsing page only")

        receipt = await self.extract_receipt_from_page(page, merchant=merchant, currency=currency)
        if pdf_path:
            receipt = Receipt(
                receipt_id=receipt.receipt_id,
                merchant=receipt.merchant,
                amount=receipt.amount,
                currency=receipt.currency,
                description=receipt.description,
                date=receipt.date,
                raw_text=receipt.raw_text,
                pdf_path=str(pdf_path),
            )
        return receipt, pdf_path

    # ------------------------------------------------------------------ #
    # Extraction helpers
    # ------------------------------------------------------------------ #

    async def _extract_amount(
        self,
        page: Page,
        selector: str | None,
        raw_text: str,
    ) -> float:
        if selector:
            try:
                text = await page.inner_text(selector)
                return self._parse_amount(text)
            except Exception:  # noqa: BLE001
                pass
        # Fallback: regex on page text
        return self._parse_amount(raw_text)

    async def _extract_id(
        self,
        page: Page,
        selector: str | None,
        raw_text: str,
    ) -> str | None:
        if selector:
            try:
                return (await page.inner_text(selector)).strip()
            except Exception:  # noqa: BLE001
                pass
        patterns = [
            r"Order\s*#?\s*([A-Z0-9\-]+)",
            r"Receipt\s*#?\s*([A-Z0-9\-]+)",
            r"Invoice\s*#?\s*([A-Z0-9\-]+)",
            r"Transaction\s*ID\s*[:#]?\s*([A-Z0-9\-]+)",
            r"Confirmation\s*#?\s*([A-Z0-9\-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw_text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    async def _extract_date(
        self,
        page: Page,
        selector: str | None,
        raw_text: str,
    ) -> datetime | None:
        if selector:
            try:
                text = (await page.inner_text(selector)).strip()
                return self._parse_date(text)
            except Exception:  # noqa: BLE001
                pass
        # Try common date patterns in raw text
        date_patterns = [
            r"(\d{1,2}/\d{1,2}/\d{2,4})",
            r"(\d{4}-\d{2}-\d{2})",
            r"([A-Za-z]+ \d{1,2},? \d{4})",
        ]
        for pattern in date_patterns:
            match = re.search(pattern, raw_text)
            if match:
                parsed = self._parse_date(match.group(1))
                if parsed:
                    return parsed
        return None

    async def _extract_description(
        self,
        page: Page,
        selector: str | None,
        raw_text: str,
    ) -> str:
        if selector:
            try:
                return (await page.inner_text(selector)).strip()
            except Exception:  # noqa: BLE001
                pass
        # Try to find a description-ish paragraph
        desc_match = re.search(r"<p[^>]*>([^<]{20,200})</p>", raw_text, re.IGNORECASE)
        if desc_match:
            return desc_match.group(1).strip()
        return ""

    @staticmethod
    def _parse_amount(text: str) -> float:
        # Extract the first currency-looking number
        # Handle $1,234.56 and 1.234,56
        match = re.search(r"[\$€£]?(\d{1,3}(?:[,\.]\d{3})*(?:[,\.]\d{2}))", text)
        if match:
            raw = match.group(1).replace(",", "")
            return float(raw)
        # Fallback: any number
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if match:
            return float(match.group(1))
        return 0.0

    @staticmethod
    def _parse_date(text: str) -> datetime | None:
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    # ------------------------------------------------------------------ #
    # Stripe checkout helpers
    # ------------------------------------------------------------------ #

    async def wait_for_stripe_confirmation(
        self,
        page: Page,
        *,
        timeout_ms: int = 60000,
    ) -> Receipt:
        """Wait for Stripe checkout success indicators and extract receipt."""
        success_selectors = [
            'text="Payment successful"',
            'text="Thank you"',
            'text="Confirmed"',
            'text="Your order is confirmed"',
            ".ConfirmationPage",
            "[data-testid='confirmation']",
        ]
        for sel in success_selectors:
            try:
                await page.wait_for_selector(sel, timeout=timeout_ms)
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            logger.warning("ReceiptDownloader: no Stripe confirmation detected")

        receipt = await self.extract_receipt_from_page(
            page,
            merchant=await page.title(),
            currency="USD",
        )
        return receipt
