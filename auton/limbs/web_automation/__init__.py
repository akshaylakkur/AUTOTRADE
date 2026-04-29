"""Web automation layer for Project ÆON.

Playwright-based browser control, form filling, navigation,
session persistence, receipt downloading, CAPTCHA solving,
and task recording.
"""

from auton.limbs.web_automation.browser_controller import BrowserController
from auton.limbs.web_automation.captcha_solver import CaptchaSolver
from auton.limbs.web_automation.dataclasses import (
    ActionStatus,
    CaptchaInfo,
    FormField,
    Receipt,
    TaskRecording,
    WebAction,
    WebActionType,
    WebResult,
)
from auton.limbs.web_automation.form_filler import FormFiller
from auton.limbs.web_automation.navigator import Navigator
from auton.limbs.web_automation.receipt_downloader import ReceiptDownloader
from auton.limbs.web_automation.session_manager import SessionManager
from auton.limbs.web_automation.task_recorder import TaskRecorder

__all__ = [
    "BrowserController",
    "CaptchaSolver",
    "FormFiller",
    "Navigator",
    "ReceiptDownloader",
    "SessionManager",
    "TaskRecorder",
    "WebAction",
    "WebActionType",
    "WebResult",
    "ActionStatus",
    "CaptchaInfo",
    "FormField",
    "Receipt",
    "TaskRecording",
]
