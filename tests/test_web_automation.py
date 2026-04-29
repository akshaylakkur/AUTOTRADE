"""Comprehensive pytest suite for auton.limbs.web_automation."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from auton.limbs.web_automation.browser_controller import BrowserController
from auton.limbs.web_automation.captcha_solver import CaptchaSolver, CaptchaSolution
from auton.limbs.web_automation.dataclasses import (
    ActionStatus,
    CaptchaInfo,
    FormField,
    Receipt,
    WebAction,
    WebActionType,
    WebResult,
)
from auton.limbs.web_automation.form_filler import FormFiller
from auton.limbs.web_automation.navigator import Navigator
from auton.limbs.web_automation.receipt_downloader import ReceiptDownloader
from auton.limbs.web_automation.session_manager import SessionManager
from auton.limbs.web_automation.task_recorder import TaskRecorder


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_page():
    page = AsyncMock()
    page.url = "https://example.com/checkout"
    return page


@pytest.fixture
def mock_context():
    ctx = AsyncMock()
    return ctx


@pytest.fixture
def mock_browser():
    browser = AsyncMock()
    return browser


@pytest.fixture
def mock_playwright(mock_browser):
    pw = MagicMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=mock_browser)
    pw.stop = AsyncMock()
    return pw


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# --------------------------------------------------------------------------- #
# BrowserController
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_browser_controller_start_stop(mock_playwright, mock_browser):
    controller = BrowserController(headless=True, browser_type="chromium")
    with patch("auton.limbs.web_automation.browser_controller.async_playwright") as mock_pw_factory:
        mock_pw_factory.return_value.start = AsyncMock(return_value=mock_playwright)
        await controller.start()
        assert controller.is_running is True
        assert controller._browser is mock_browser
        await controller.stop()
        assert controller.is_running is False


@pytest.mark.asyncio
async def test_browser_controller_new_context(mock_playwright, mock_browser):
    controller = BrowserController()
    mock_ctx = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_ctx)
    with patch("auton.limbs.web_automation.browser_controller.async_playwright") as mock_pw_factory:
        mock_pw_factory.return_value.start = AsyncMock(return_value=mock_playwright)
        await controller.start()
        cid = await controller.new_context(context_id="test_ctx")
        assert cid == "test_ctx"
        assert "test_ctx" in controller.list_contexts()
        await controller.close_context("test_ctx")


@pytest.mark.asyncio
async def test_browser_controller_new_page(mock_playwright, mock_browser):
    controller = BrowserController()
    mock_ctx = AsyncMock()
    mock_page = AsyncMock()
    mock_page.context = mock_ctx
    mock_ctx.new_page = AsyncMock(return_value=mock_page)
    mock_browser.new_context = AsyncMock(return_value=mock_ctx)
    with patch("auton.limbs.web_automation.browser_controller.async_playwright") as mock_pw_factory:
        mock_pw_factory.return_value.start = AsyncMock(return_value=mock_playwright)
        await controller.start()
        # Ensure context is registered before creating page
        await controller.new_context(context_id="test_ctx")
        pid, page = await controller.new_page(context_id="test_ctx")
        assert pid.startswith("page_")
        assert page is mock_page
        assert pid in controller.list_pages()


@pytest.mark.asyncio
async def test_browser_controller_get_page_raises_when_missing(mock_playwright, mock_browser):
    controller = BrowserController()
    with patch("auton.limbs.web_automation.browser_controller.async_playwright") as mock_pw_factory:
        mock_pw_factory.return_value.start = AsyncMock(return_value=mock_playwright)
        await controller.start()
        with pytest.raises(KeyError):
            controller.get_page("nonexistent")


@pytest.mark.asyncio
async def test_browser_controller_with_retry_success():
    controller = BrowserController()
    coro = AsyncMock(return_value="ok")
    result = await controller.with_retry(lambda: coro(), max_retries=2)
    assert result == "ok"


@pytest.mark.asyncio
async def test_browser_controller_with_retry_eventually_succeeds():
    controller = BrowserController()
    call_count = 0

    async def _coro():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("fail")
        return "ok"

    result = await controller.with_retry(_coro, max_retries=2, base_delay=0.01)
    assert result == "ok"


@pytest.mark.asyncio
async def test_browser_controller_with_retry_exhausted():
    controller = BrowserController()

    async def _coro():
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError, match="fail"):
        await controller.with_retry(_coro, max_retries=1, base_delay=0.01)


@pytest.mark.asyncio
async def test_browser_controller_context_manager(mock_playwright, mock_browser):
    controller = BrowserController()
    with patch("auton.limbs.web_automation.browser_controller.async_playwright") as mock_pw_factory:
        mock_pw_factory.return_value.start = AsyncMock(return_value=mock_playwright)
        async with controller:
            assert controller.is_running is True
        assert controller.is_running is False


# --------------------------------------------------------------------------- #
# Navigator
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_navigator_goto(mock_page):
    nav = Navigator(mock_page)
    await nav.goto("https://example.com", wait_until="networkidle", timeout_ms=10000)
    mock_page.goto.assert_awaited_once_with("https://example.com", wait_until="networkidle", timeout=10000)


@pytest.mark.asyncio
async def test_navigator_click(mock_page):
    nav = Navigator(mock_page)
    await nav.click("#submit", timeout_ms=5000, force=True, delay_ms=100)
    mock_page.click.assert_awaited_once_with("#submit", timeout=5000, force=True, delay=100)


@pytest.mark.asyncio
async def test_navigator_click_if_visible(mock_page):
    nav = Navigator(mock_page)
    mock_page.wait_for_selector = AsyncMock(return_value=MagicMock())
    result = await nav.click_if_visible("#btn", timeout_ms=2000)
    assert result is True
    mock_page.click.assert_awaited_once_with("#btn")


@pytest.mark.asyncio
async def test_navigator_click_if_visible_not_found(mock_page):
    nav = Navigator(mock_page)
    mock_page.wait_for_selector = AsyncMock(side_effect=Exception("timeout"))
    result = await nav.click_if_visible("#btn", timeout_ms=100)
    assert result is False


@pytest.mark.asyncio
async def test_navigator_scroll_to(mock_page):
    nav = Navigator(mock_page)
    element = AsyncMock()
    mock_page.wait_for_selector = AsyncMock(return_value=element)
    await nav.scroll_to("#footer")
    element.scroll_into_view_if_needed.assert_awaited_once()


@pytest.mark.asyncio
async def test_navigator_scroll_page(mock_page):
    nav = Navigator(mock_page)
    await nav.scroll_page(direction="down", distance=300)
    mock_page.evaluate.assert_awaited_once_with("window.scrollBy(0, 300)")


@pytest.mark.asyncio
async def test_navigator_wait_for_selector(mock_page):
    nav = Navigator(mock_page)
    mock_page.wait_for_selector = AsyncMock(return_value=MagicMock())
    result = await nav.wait_for_selector("#modal", state="visible", timeout_ms=5000)
    assert result is not None


@pytest.mark.asyncio
async def test_navigator_get_text(mock_page):
    nav = Navigator(mock_page)
    element = AsyncMock()
    element.inner_text = AsyncMock(return_value="Hello")
    mock_page.query_selector = AsyncMock(return_value=element)
    text = await nav.get_text("#msg")
    assert text == "Hello"


@pytest.mark.asyncio
async def test_navigator_is_visible(mock_page):
    nav = Navigator(mock_page)
    element = AsyncMock()
    element.is_visible = AsyncMock(return_value=True)
    mock_page.query_selector = AsyncMock(return_value=element)
    assert await nav.is_visible("#msg") is True


@pytest.mark.asyncio
async def test_navigator_is_visible_not_found(mock_page):
    nav = Navigator(mock_page)
    mock_page.query_selector = AsyncMock(return_value=None)
    assert await nav.is_visible("#msg") is False


# --------------------------------------------------------------------------- #
# FormFiller
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_form_filler_fill_text(mock_page):
    ff = FormFiller(mock_page)
    field = FormField(name="email", value="test@example.com", field_type="email", selector='input[name="email"]')
    sel = await ff.fill_field(field)
    assert sel == 'input[name="email"]'
    mock_page.fill.assert_awaited_once_with('input[name="email"]', "test@example.com", timeout=10000)


@pytest.mark.asyncio
async def test_form_filler_fill_select(mock_page):
    ff = FormFiller(mock_page)
    field = FormField(name="plan", value="Pro", field_type="select", selector='select[name="plan"]')
    await ff.fill_field(field)
    mock_page.select_option.assert_awaited_once_with('select[name="plan"]', label="Pro", timeout=10000)


@pytest.mark.asyncio
async def test_form_filler_fill_checkbox(mock_page):
    ff = FormFiller(mock_page)
    element = AsyncMock()
    element.is_checked = AsyncMock(return_value=False)
    mock_page.wait_for_selector = AsyncMock(return_value=element)
    field = FormField(name="terms", value="true", field_type="checkbox", selector='input[name="terms"]')
    await ff.fill_field(field)
    element.check.assert_awaited_once()


@pytest.mark.asyncio
async def test_form_filler_fill_file(mock_page, temp_dir):
    ff = FormFiller(mock_page)
    file_path = temp_dir / "test.txt"
    file_path.write_text("hello")
    field = FormField(name="resume", value=str(file_path), field_type="file", selector='input[name="resume"]')
    await ff.fill_field(field)
    mock_page.set_input_files.assert_awaited_once()


@pytest.mark.asyncio
async def test_form_filler_create_account(mock_page):
    # Set up element mock so validation doesn't produce unawaited coroutine warnings
    element = MagicMock()
    element.input_value = AsyncMock(return_value="filled")
    mock_page.wait_for_selector = AsyncMock(return_value=element)
    ff = FormFiller(mock_page)
    result = await ff.create_account(
        email="user@example.com",
        password="secret123",
        username="user",
        submit_by_text="Sign up",
    )
    assert result["submitted"] is True
    calls = mock_page.fill.await_args_list
    # email field_type triggers type-based selectors first
    assert any(
        c[0][0] in ('input[type="email"]', 'input[name="email"]')
        and c[0][1] == "user@example.com"
        for c in calls
    )


@pytest.mark.asyncio
async def test_form_filler_fill_payment_form(mock_page):
    element = MagicMock()
    element.input_value = AsyncMock(return_value="filled")
    mock_page.wait_for_selector = AsyncMock(return_value=element)
    ff = FormFiller(mock_page)
    result = await ff.fill_payment_form(
        card_number="4111111111111111",
        expiry="12/25",
        cvv="123",
        name_on_card="Test User",
        submit_by_text="Pay",
    )
    assert result["submitted"] is True
    calls = mock_page.fill.await_args_list
    assert any(c[0][1] == "4111111111111111" for c in calls)


@pytest.mark.asyncio
async def test_form_filler_fill_form_with_submit(mock_page):
    element = MagicMock()
    element.input_value = AsyncMock(return_value="filled")
    mock_page.wait_for_selector = AsyncMock(return_value=element)
    ff = FormFiller(mock_page)
    fields = [
        FormField(name="name", value="Alice", selector='input[name="name"]'),
        FormField(name="age", value="30", selector='input[name="age"]'),
    ]
    result = await ff.fill_form(fields, submit_selector='button[type="submit"]')
    assert result["submitted"] is True
    assert len(result["resolved_selectors"]) == 2


# --------------------------------------------------------------------------- #
# SessionManager
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_session_manager_save_and_load(temp_dir):
    sm = SessionManager(storage_dir=temp_dir)
    ctx = AsyncMock()
    state = {"cookies": [{"name": "session", "value": "abc123"}], "origins": []}
    ctx.storage_state = AsyncMock(return_value=state)
    path = await sm.save_session(ctx, "test_session")
    assert path.exists()
    loaded = sm.load_session_state("test_session")
    assert loaded == state


@pytest.mark.asyncio
async def test_session_manager_local_storage(mock_page):
    sm = SessionManager()
    mock_page.evaluate = AsyncMock(return_value={"key1": "val1", "key2": "val2"})
    result = await sm.get_local_storage(mock_page)
    assert result == {"key1": "val1", "key2": "val2"}


@pytest.mark.asyncio
async def test_session_manager_set_local_storage(mock_page):
    sm = SessionManager()
    await sm.set_local_storage(mock_page, {"theme": "dark"})
    assert mock_page.evaluate.await_count == 1


@pytest.mark.asyncio
async def test_session_manager_cookies(mock_context):
    sm = SessionManager()
    mock_context.cookies = AsyncMock(return_value=[{"name": "sid", "value": "xyz"}])
    cookies = await sm.get_cookies(mock_context)
    assert cookies == [{"name": "sid", "value": "xyz"}]


@pytest.mark.asyncio
async def test_session_manager_persist_and_restore_named(mock_page, temp_dir):
    sm = SessionManager(storage_dir=temp_dir)
    ctx = AsyncMock()
    ctx.storage_state = AsyncMock(return_value={"cookies": []})
    mock_page.evaluate = AsyncMock(return_value={})
    path = await sm.persist_named_session(ctx, mock_page, "named")
    assert path.exists()
    restored = sm.restore_named_session("named")
    assert restored == {"cookies": []}


def test_session_manager_list_and_delete(temp_dir):
    sm = SessionManager(storage_dir=temp_dir)
    (temp_dir / "session_a.json").write_text("{}")
    (temp_dir / "session_b.json").write_text("{}")
    names = sm.list_sessions()
    assert sorted(names) == ["session_a", "session_b"]
    assert sm.delete_session("session_a") is True
    assert sm.delete_session("session_a") is False


# --------------------------------------------------------------------------- #
# ReceiptDownloader
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_receipt_downloader_extract_amount():
    rd = ReceiptDownloader()
    assert rd._parse_amount("Total: $49.99") == 49.99
    assert rd._parse_amount("Price: €1,234.56") == 1234.56
    assert rd._parse_amount("Free") == 0.0


def test_receipt_downloader_parse_date():
    rd = ReceiptDownloader()
    dt = rd._parse_date("04/27/2026")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 4
    assert dt.day == 27


@pytest.mark.asyncio
async def test_receipt_downloader_extract_receipt_from_page(mock_page):
    rd = ReceiptDownloader()
    mock_page.content = AsyncMock(return_value="<html><body>Order #ABC123 Total: $99.00</body></html>")
    mock_page.title = AsyncMock(return_value="Example Store")
    receipt = await rd.extract_receipt_from_page(mock_page)
    assert isinstance(receipt, Receipt)
    assert receipt.receipt_id == "ABC123"
    assert receipt.amount == 99.0
    assert receipt.merchant == "Example Store"


@pytest.mark.asyncio
async def test_receipt_downloader_wait_for_stripe_confirmation(mock_page):
    rd = ReceiptDownloader()
    mock_page.wait_for_selector = AsyncMock(return_value=MagicMock())
    mock_page.content = AsyncMock(return_value="<html>Payment successful</html>")
    mock_page.title = AsyncMock(return_value="Stripe Checkout")
    receipt = await rd.wait_for_stripe_confirmation(mock_page, timeout_ms=5000)
    assert receipt.merchant == "Stripe Checkout"
    assert receipt.amount == 0.0


# --------------------------------------------------------------------------- #
# CaptchaSolver
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_captcha_solver_detect_recaptcha(mock_page):
    solver = CaptchaSolver(api_key="fake_key")
    iframe = AsyncMock()
    iframe.get_attribute = AsyncMock(return_value="https://google.com/recaptcha/api2/anchor?k=sitekey123")
    mock_page.query_selector = AsyncMock(return_value=iframe)
    info = await solver.detect(mock_page)
    assert info is not None
    assert info.captcha_type == "recaptcha_v2"
    assert info.site_key == "sitekey123"


@pytest.mark.asyncio
async def test_captcha_solver_detect_none(mock_page):
    solver = CaptchaSolver()
    mock_page.query_selector = AsyncMock(return_value=None)
    info = await solver.detect(mock_page)
    assert info is None


@pytest.mark.asyncio
async def test_captcha_solver_solve_dummy_mode():
    solver = CaptchaSolver(api_key=None)
    info = CaptchaInfo(captcha_type="recaptcha_v2", site_key="sk")
    solution = await solver.solve(info)
    assert solution.token == "DUMMY_TOKEN"
    assert solution.cost == 0.0


@pytest.mark.asyncio
async def test_captcha_solver_solve_on_page(mock_page):
    solver = CaptchaSolver(api_key="fake_key")
    iframe = AsyncMock()
    iframe.get_attribute = AsyncMock(return_value="https://google.com/recaptcha/api2/anchor?k=sk")
    mock_page.query_selector = AsyncMock(return_value=iframe)
    solution = await solver.solve_on_page(mock_page)
    assert solution is not None
    assert solution.token.startswith("FAKE")


def test_captcha_solver_extract_param():
    solver = CaptchaSolver()
    assert solver._extract_param("https://example.com?k=abc&x=1", "k") == "abc"
    assert solver._extract_param("https://example.com?x=1", "k") is None


def test_captcha_solver_estimate_cost():
    solver = CaptchaSolver()
    assert solver._estimate_cost(CaptchaInfo(captcha_type="recaptcha_v2")) == 0.002
    assert solver._estimate_cost(CaptchaInfo(captcha_type="unknown")) == 0.002


# --------------------------------------------------------------------------- #
# TaskRecorder
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_task_recorder_record(mock_page, temp_dir):
    tr = TaskRecorder(output_dir=temp_dir, capture_screenshots=True)
    action = WebAction(action_type=WebActionType.NAVIGATE, url="https://example.com")
    result = WebResult(action=action, success=True, status=ActionStatus.SUCCESS, duration_ms=100.0)
    mock_page.screenshot = AsyncMock()
    recording = await tr.record(action, result, page=mock_page)
    assert recording.action == action
    assert recording.result == result
    assert recording.screenshot_path is not None
    assert len(tr.get_recordings()) == 1


@pytest.mark.asyncio
async def test_task_recorder_record_no_screenshot(temp_dir):
    tr = TaskRecorder(output_dir=temp_dir, capture_screenshots=False)
    action = WebAction(action_type=WebActionType.CLICK, selector="#btn")
    result = WebResult(action=action, success=True, status=ActionStatus.SUCCESS)
    recording = await tr.record(action, result)
    assert recording.screenshot_path is None


@pytest.mark.asyncio
async def test_task_recorder_summary(mock_page, temp_dir):
    tr = TaskRecorder(output_dir=temp_dir)
    for i in range(3):
        action = WebAction(action_type=WebActionType.CLICK)
        result = WebResult(action=action, success=(i != 1), status=ActionStatus.SUCCESS, duration_ms=100.0)
        await tr.record(action, result)
    summary = tr.summary()
    assert summary["total_actions"] == 3
    assert summary["successful"] == 2
    assert summary["failed"] == 1


@pytest.mark.asyncio
async def test_task_recorder_export_json(mock_page, temp_dir):
    tr = TaskRecorder(output_dir=temp_dir)
    action = WebAction(action_type=WebActionType.NAVIGATE, url="https://example.com")
    result = WebResult(action=action, success=True, status=ActionStatus.SUCCESS, duration_ms=150.0)
    await tr.record(action, result)
    path = tr.export_json()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["summary"]["total_actions"] == 1
    assert len(data["recordings"]) == 1


def test_task_recorder_clear(temp_dir):
    tr = TaskRecorder(output_dir=temp_dir)
    tr._recordings.append(MagicMock())
    tr.clear()
    assert len(tr.get_recordings()) == 0


# --------------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------------- #


def test_web_action_creation():
    action = WebAction(
        action_type=WebActionType.NAVIGATE,
        url="https://example.com",
        selector="#btn",
        value="click",
        options={"wait": True},
        priority=1,
        max_retries=5,
        timeout_ms=10000,
    )
    assert action.action_type == WebActionType.NAVIGATE
    assert action.url == "https://example.com"
    assert action.timeout_ms == 10000


def test_web_result_creation():
    action = WebAction(action_type=WebActionType.CLICK)
    result = WebResult(action=action, success=True, status=ActionStatus.SUCCESS)
    assert result.success is True
    assert result.status == ActionStatus.SUCCESS


def test_form_field_creation():
    field = FormField(name="email", value="a@b.com", field_type="email", selector="input#email")
    assert field.required is True
    assert field.field_type == "email"


def test_receipt_creation():
    from datetime import datetime, timezone

    receipt = Receipt(
        receipt_id="R1",
        merchant="Store",
        amount=10.0,
        currency="USD",
        description="Test",
        date=datetime.now(timezone.utc),
        raw_text="raw",
    )
    assert receipt.receipt_id == "R1"
    assert receipt.amount == 10.0
