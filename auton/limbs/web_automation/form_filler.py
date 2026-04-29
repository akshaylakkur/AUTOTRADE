"""Form filler for web automation — intelligently fill forms."""

from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .dataclasses import FormField

logger = logging.getLogger(__name__)


class FormFiller:
    """Intelligent form filler with selector inference and validation."""

    def __init__(self, page: Page) -> None:
        self._page = page

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def fill_form(
        self,
        fields: list[FormField],
        *,
        submit_selector: str | None = None,
        submit_by_text: str | None = None,
        validate: bool = True,
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Fill a form with the given fields.

        Parameters
        ----------
        fields:
            List of :class:`FormField` descriptors.
        submit_selector:
            Optional selector for the submit button to click after filling.
        submit_by_text:
            Alternative: click button containing this text.
        validate:
            If ``True``, verify each field has a value after filling.
        timeout_ms:
            Global timeout for the operation.

        Returns
        -------
        dict mapping field names to their resolved selectors.
        """
        resolved: dict[str, str] = {}
        for field in fields:
            sel = field.selector or await self._infer_selector(field)
            await self._fill_field(field, sel, timeout_ms)
            resolved[field.name] = sel

        if submit_selector:
            await self._page.click(submit_selector, timeout=timeout_ms)
        elif submit_by_text:
            await self._page.click(f'button:has-text("{submit_by_text}")', timeout=timeout_ms)

        if validate:
            await self._validate_filled(fields, resolved, timeout_ms)

        logger.debug("FormFiller: filled %d fields", len(fields))
        return {"resolved_selectors": resolved, "submitted": bool(submit_selector or submit_by_text)}

    async def fill_field(self, field: FormField, *, timeout_ms: int = 10000) -> str:
        """Fill a single field and return the selector used."""
        sel = field.selector or await self._infer_selector(field)
        await self._fill_field(field, sel, timeout_ms)
        return sel

    # ------------------------------------------------------------------ #
    # Field inference
    # ------------------------------------------------------------------ #

    async def _infer_selector(self, field: FormField) -> str:
        """Heuristic selector inference from field name and type."""
        name = field.name

        # Try exact match strategies in order
        strategies = [
            f'input[name="{name}"]',
            f'textarea[name="{name}"]',
            f'select[name="{name}"]',
            f'input[id="{name}"]',
            f'label:has-text("{name}") + input',
            f'label:has-text("{name}") + textarea',
            f'label:has-text("{name}") + select',
            f'input[placeholder*="{name}" i]',
            f'input[aria-label*="{name}" i]',
            f'[data-testid="{name}"]',
            f'[data-field="{name}"]',
        ]

        # For email/password/credit card, add type-based selectors
        type_strategies: dict[str, list[str]] = {
            "email": [
                'input[type="email"]',
                'input[name*="email" i]',
                'input[id*="email" i]',
            ],
            "password": [
                'input[type="password"]',
                'input[name*="password" i]',
                'input[name*="pass" i]',
            ],
            "file": [
                'input[type="file"]',
            ],
            "checkbox": [
                'input[type="checkbox"]',
            ],
            "card_number": [
                'input[name*="card" i]',
                'input[name*="cc-number" i]',
                'input[id*="card" i]',
            ],
            "expiry": [
                'input[name*="exp" i]',
                'input[name*="expiration" i]',
                'input[id*="exp" i]',
            ],
            "cvv": [
                'input[name*="cvv" i]',
                'input[name*="cvc" i]',
                'input[name*="security" i]',
            ],
        }

        if field.field_type in type_strategies:
            strategies = type_strategies[field.field_type] + strategies

        for selector in strategies:
            try:
                element = await self._page.wait_for_selector(selector, timeout=2000, state="attached")
                if element:
                    logger.debug("FormFiller: inferred selector %s for %s", selector, name)
                    return selector
            except Exception:  # noqa: BLE001
                continue

        raise RuntimeError(f"Could not infer selector for field {name!r}")

    async def _fill_field(
        self,
        field: FormField,
        selector: str,
        timeout_ms: int,
    ) -> None:
        """Dispatch to the appropriate fill method by field type."""
        ft = field.field_type.lower()
        if ft in ("text", "email", "password", "tel", "url", "search", "card_number", "expiry", "cvv"):
            await self._fill_text(selector, field.value, timeout_ms)
        elif ft == "textarea":
            await self._fill_textarea(selector, field.value, timeout_ms)
        elif ft == "select":
            await self._fill_select(selector, field.value, timeout_ms)
        elif ft == "checkbox":
            await self._fill_checkbox(selector, field.value, timeout_ms)
        elif ft == "radio":
            await self._fill_radio(selector, field.value, timeout_ms)
        elif ft == "file":
            await self._fill_file(selector, field.value, timeout_ms)
        elif ft == "number":
            await self._fill_number(selector, field.value, timeout_ms)
        elif ft == "date":
            await self._fill_date(selector, field.value, timeout_ms)
        else:
            # Fallback to text
            await self._fill_text(selector, field.value, timeout_ms)

    # ------------------------------------------------------------------ #
    # Fill implementations
    # ------------------------------------------------------------------ #

    async def _fill_text(self, selector: str, value: str, timeout_ms: int) -> None:
        await self._page.fill(selector, value, timeout=timeout_ms)

    async def _fill_textarea(self, selector: str, value: str, timeout_ms: int) -> None:
        await self._page.fill(selector, value, timeout=timeout_ms)

    async def _fill_select(self, selector: str, value: str, timeout_ms: int) -> None:
        # Try by label first, then by value
        try:
            await self._page.select_option(selector, label=value, timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            await self._page.select_option(selector, value=value, timeout=timeout_ms)

    async def _fill_checkbox(self, selector: str, value: str, timeout_ms: int) -> None:
        should_check = value.lower() in ("true", "1", "yes", "on", "checked")
        element = await self._page.wait_for_selector(selector, timeout=timeout_ms)
        if element is None:
            raise RuntimeError(f"Checkbox {selector!r} not found")
        is_checked = await element.is_checked()
        if should_check and not is_checked:
            await element.check()
        elif not should_check and is_checked:
            await element.uncheck()

    async def _fill_radio(self, selector: str, value: str, timeout_ms: int) -> None:
        # selector may be a group; value is the radio value or label text
        try:
            await self._page.check(f'{selector}[value="{value}"]', timeout=timeout_ms)
        except Exception:  # noqa: BLE001
            # Try by associated label
            await self._page.click(f'label:has-text("{value}")', timeout=timeout_ms)

    async def _fill_file(self, selector: str, value: str, timeout_ms: int) -> None:
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Upload file not found: {value}")
        mime, _ = mimetypes.guess_type(str(path))
        await self._page.set_input_files(
            selector,
            {"name": path.name, "mimeType": mime or "application/octet-stream", "buffer": path.read_bytes()},
            timeout=timeout_ms,
        )

    async def _fill_number(self, selector: str, value: str, timeout_ms: int) -> None:
        await self._page.fill(selector, value, timeout=timeout_ms)

    async def _fill_date(self, selector: str, value: str, timeout_ms: int) -> None:
        # value should be ISO date (YYYY-MM-DD)
        await self._page.fill(selector, value, timeout=timeout_ms)

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    async def _validate_filled(
        self,
        fields: list[FormField],
        resolved: dict[str, str],
        timeout_ms: int,
    ) -> None:
        """Verify that required fields have values."""
        for field in fields:
            if not field.required:
                continue
            sel = resolved[field.name]
            try:
                element = await self._page.wait_for_selector(sel, timeout=min(5000, timeout_ms))
                if element is None:
                    raise RuntimeError(f"Validation failed: {field.name} element not found")
                val = await element.input_value()
                if not val.strip():
                    raise RuntimeError(f"Validation failed: {field.name} is empty")
            except Exception as exc:  # noqa: BLE001
                if "is empty" in str(exc):
                    raise
                # Non-input elements (like selects) are harder to validate generically;
                # skip if we can't read input_value.
                pass

    # ------------------------------------------------------------------ #
    # Account creation helpers
    # ------------------------------------------------------------------ #

    async def create_account(
        self,
        *,
        email: str,
        password: str,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        additional_fields: list[FormField] | None = None,
        submit_selector: str | None = None,
        submit_by_text: str = "Sign up",
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Fill a standard account-creation form."""
        fields: list[FormField] = [
            FormField(name="email", value=email, field_type="email"),
            FormField(name="password", value=password, field_type="password"),
        ]
        if username:
            fields.append(FormField(name="username", value=username))
        if first_name:
            fields.append(FormField(name="first_name", value=first_name))
        if last_name:
            fields.append(FormField(name="last_name", value=last_name))
        if additional_fields:
            fields.extend(additional_fields)

        result = await self.fill_form(
            fields,
            submit_selector=submit_selector,
            submit_by_text=submit_by_text if not submit_selector else None,
            validate=True,
            timeout_ms=timeout_ms,
        )
        logger.info("FormFiller: account created for %s", email)
        return result

    # ------------------------------------------------------------------ #
    # Payment helpers
    # ------------------------------------------------------------------ #

    async def fill_payment_form(
        self,
        *,
        card_number: str,
        expiry: str,
        cvv: str,
        name_on_card: str | None = None,
        zip_code: str | None = None,
        submit_selector: str | None = None,
        submit_by_text: str = "Pay",
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Fill a standard credit-card payment form."""
        fields: list[FormField] = [
            FormField(name="card_number", value=card_number, field_type="card_number"),
            FormField(name="expiry", value=expiry, field_type="expiry"),
            FormField(name="cvv", value=cvv, field_type="cvv"),
        ]
        if name_on_card:
            fields.append(FormField(name="name_on_card", value=name_on_card))
        if zip_code:
            fields.append(FormField(name="zip_code", value=zip_code))

        result = await self.fill_form(
            fields,
            submit_selector=submit_selector,
            submit_by_text=submit_by_text if not submit_selector else None,
            validate=True,
            timeout_ms=timeout_ms,
        )
        logger.info("FormFiller: payment form filled")
        return result
