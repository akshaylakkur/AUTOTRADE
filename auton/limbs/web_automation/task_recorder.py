"""Task recorder for web automation audit trail.

Logs all actions with screenshots for later review.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .dataclasses import TaskRecording, WebAction, WebResult

logger = logging.getLogger(__name__)


class TaskRecorder:
    """Records every action and result during a web automation task.

    Parameters
    ----------
    output_dir:
        Directory where screenshots and JSON logs are saved.
    capture_screenshots:
        If ``True``, capture a screenshot after each action.
    """

    def __init__(
        self,
        output_dir: str | Path = "data/web_audit",
        capture_screenshots: bool = True,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._capture_screenshots = capture_screenshots
        self._recordings: list[TaskRecording] = []
        self._task_id = str(uuid.uuid4())
        self._started_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #

    async def record(
        self,
        action: WebAction,
        result: WebResult,
        page: Page | None = None,
    ) -> TaskRecording:
        """Record a single step. Captures screenshot if *page* is provided."""
        screenshot_path: str | None = None
        if self._capture_screenshots and page is not None:
            try:
                screenshot_path = await self._take_screenshot(page, action)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TaskRecorder: screenshot failed: %s", exc)

        recording = TaskRecording(
            action=action,
            result=result,
            screenshot_path=screenshot_path,
            timestamp=datetime.now(timezone.utc),
        )
        self._recordings.append(recording)
        self._append_jsonl(recording)
        return recording

    async def _take_screenshot(self, page: Page, action: WebAction) -> str | None:
        safe_type = action.action_type.value.replace(" ", "_")
        filename = f"{self._task_id}_{len(self._recordings):04d}_{safe_type}.png"
        path = self._output_dir / filename
        await page.screenshot(path=str(path), full_page=False)
        return str(path)

    def _append_jsonl(self, recording: TaskRecording) -> None:
        log_path = self._output_dir / f"{self._task_id}.jsonl"
        entry = {
            "timestamp": recording.timestamp.isoformat(),
            "action": {
                "type": recording.action.action_type.value,
                "url": recording.action.url,
                "selector": recording.action.selector,
                "value": recording.action.value,
                "options": recording.action.options,
            },
            "result": {
                "success": recording.result.success,
                "status": recording.result.status.value,
                "error_message": recording.result.error_message,
                "duration_ms": recording.result.duration_ms,
            },
            "screenshot_path": recording.screenshot_path,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------------ #
    # Summary / export
    # ------------------------------------------------------------------ #

    def get_recordings(self) -> list[TaskRecording]:
        """Return all recordings for this task."""
        return list(self._recordings)

    def summary(self) -> dict[str, Any]:
        """Return a summary of the task execution."""
        total = len(self._recordings)
        success = sum(1 for r in self._recordings if r.result.success)
        failed = total - success
        durations = [r.result.duration_ms for r in self._recordings]
        return {
            "task_id": self._task_id,
            "started_at": self._started_at.isoformat(),
            "total_actions": total,
            "successful": success,
            "failed": failed,
            "total_duration_ms": sum(durations),
            "avg_duration_ms": (sum(durations) / len(durations)) if durations else 0.0,
        }

    def export_json(self, path: str | Path | None = None) -> Path:
        """Export the full task log as a single JSON file."""
        dest = Path(path) if path else self._output_dir / f"{self._task_id}_export.json"
        data = {
            "task_id": self._task_id,
            "started_at": self._started_at.isoformat(),
            "summary": self.summary(),
            "recordings": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "action_type": r.action.action_type.value,
                    "url": r.action.url,
                    "selector": r.action.selector,
                    "value": r.action.value,
                    "success": r.result.success,
                    "status": r.result.status.value,
                    "error_message": r.result.error_message,
                    "duration_ms": r.result.duration_ms,
                    "screenshot_path": r.screenshot_path,
                }
                for r in self._recordings
            ],
        }
        dest.write_text(json.dumps(data, indent=2))
        return dest

    def clear(self) -> None:
        """Clear in-memory recordings."""
        self._recordings.clear()
