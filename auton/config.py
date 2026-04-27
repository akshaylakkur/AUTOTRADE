"""Configuration stub for AEON."""

import json
from pathlib import Path
from typing import Any


class Config:
    """Minimal configuration container."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = data or {}

    @classmethod
    def from_path(cls, path: str) -> "Config":
        p = Path(path)
        if p.exists():
            with open(p) as f:
                return cls(json.load(f))
        return cls()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
