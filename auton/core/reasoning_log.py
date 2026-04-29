"""Natural language reasoning log for ÆON.

A lightweight, rotating log that records what the agent is thinking, planning,
deciding, reflecting on, and warning about in real time.  Multiple modules share
a single instance via module-level singleton access.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

DEFAULT_LOG_PATH = "data/reasoning.log"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 3


class ReasoningLog:
    """Lightweight reasoning logger with log rotation."""

    _instance: ReasoningLog | None = None

    def __new__(cls, log_path: str = DEFAULT_LOG_PATH) -> ReasoningLog:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_logger(log_path)
        return cls._instance

    def _init_logger(self, log_path: str) -> None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("aeon.reasoning")
        self._logger.setLevel(logging.DEBUG)

        # Avoid duplicate handlers if re-instantiated in the same process
        if not self._logger.handlers:
            handler = RotatingFileHandler(
                log_path,
                maxBytes=MAX_BYTES,
                backupCount=BACKUP_COUNT,
            )
            handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def think(self, message: str) -> None:
        """Log a spontaneous thought or observation."""
        self._logger.info("[THINK] %s", message)

    def plan(self, message: str) -> None:
        """Log a strategic or tactical plan."""
        self._logger.info("[PLAN] %s", message)

    def decide(self, message: str) -> None:
        """Log a concrete decision with rationale."""
        self._logger.info("[DECIDE] %s", message)

    def reflect(self, message: str) -> None:
        """Log self-reflection, adaptation, or post-mortem analysis."""
        self._logger.info("[REFLECT] %s", message)

    def warn(self, message: str) -> None:
        """Log a warning or concern."""
        self._logger.warning("[WARN] %s", message)


# Module-level singleton for convenient imports.
_reasoning_log: ReasoningLog | None = None


def get_reasoning_log() -> ReasoningLog:
    """Return the shared :class:`ReasoningLog` singleton."""
    global _reasoning_log
    if _reasoning_log is None:
        _reasoning_log = ReasoningLog()
    return _reasoning_log
