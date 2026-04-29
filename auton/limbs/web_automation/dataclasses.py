"""Data classes for web automation actions and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any


class WebActionType(str, Enum):
    """Types of web actions the automation layer can perform."""

    NAVIGATE = "navigate"
    CLICK = "click"
    SCROLL = "scroll"
    TYPE = "type"
    SELECT = "select"
    UPLOAD = "upload"
    SUBMIT = "submit"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    DOWNLOAD = "download"
    EXTRACT = "extract"
    CAPTCHA_SOLVE = "captcha_solve"
    CREATE_ACCOUNT = "create_account"
    PURCHASE_SUBSCRIPTION = "purchase_subscription"
    FILL_PAYMENT = "fill_payment"


class ActionStatus(str, Enum):
    """Execution status of a web action."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class WebAction:
    """A single atomic web action queued for execution."""

    action_type: WebActionType
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    max_retries: int = 3
    timeout_ms: int = 30000


@dataclass(frozen=True, slots=True)
class WebResult:
    """Outcome of executing a web action."""

    action: WebAction
    success: bool
    status: ActionStatus
    data: dict[str, Any] = field(default_factory=dict)
    screenshot_path: str | None = None
    error_message: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class CaptchaInfo:
    """Detected CAPTCHA information."""

    captcha_type: str  # e.g. "recaptcha_v2", "hcaptcha", "image_captcha"
    site_key: str | None = None
    url: str | None = None
    iframe_selector: str | None = None
    image_url: str | None = None


@dataclass(frozen=True, slots=True)
class TaskRecording:
    """A recorded step in a task execution."""

    action: WebAction
    result: WebResult
    screenshot_path: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class Receipt:
    """Parsed receipt/invoice downloaded from a website."""

    receipt_id: str
    merchant: str
    amount: float
    currency: str
    description: str
    date: datetime
    raw_text: str
    pdf_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FormField:
    """Descriptor for a form field to be filled."""

    name: str
    value: str
    field_type: str = "text"  # text, password, email, select, checkbox, file, etc.
    selector: str | None = None
    required: bool = True
