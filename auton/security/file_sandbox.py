"""File system sandbox for Project ÆON."""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .exceptions import FileAccessDenied
from .config import FileAccessLog


class FileSandbox:
    """Restrict filesystem access so ÆON can only write to designated directories.

    Reads are permitted anywhere but audited. Auto-redacts secrets/PII from
    any file output.
    """

    WRITE_ROOTS: set[str] = {"data/", "auton/limbs/", "logs/", "cold_storage/audit/"}
    IMMUTABLE_DENY_LIST: set[str] = {
        "auton/terminal.py",
        "auton/core/",
        ".env",
    }

    # Default secret patterns
    _DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
        r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]+['\"]",
        r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*=\s*['\"][^'\"]+['\"]",
        r"(?i)(token|bearer)\s+['\"]?[a-zA-Z0-9_\-]{20,}['\"]?",
        r"(?i)(aws_access_key_id|aws_secret_access_key)\s*=\s*['\"][^'\"]+['\"]",
        r"(?i)(private[_-]?key|ssh[_-]?key)\s*=\s*['\"][^'\"]+['\"]",
        r"(?i)(authorization:\s*bearer\s+)[a-zA-Z0-9_\-\.=]+",
        r"(?i)(sk-[a-zA-Z0-9]{20,})",
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        r"\b[0-9]{4}[\s-]?[0-9]{4}[\s-]?[0-9]{4}[\s-]?[0-9]{4}\b",
        r"\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b",
    )

    _REDACTION_MASK: str = "[REDACTED]"

    def __init__(
        self,
        audit_log=None,
        write_roots: Sequence[str] | None = None,
        secret_patterns: Sequence[str] | None = None,
    ) -> None:
        self._audit_log = audit_log
        if write_roots:
            self._write_roots = set(write_roots)
        else:
            self._write_roots = set(self.WRITE_ROOTS)
        self._secret_patterns = [re.compile(p) for p in (secret_patterns or self._DEFAULT_SECRET_PATTERNS)]

    def _resolve(self, path: str | Path) -> Path:
        """Resolve symlinks to prevent escapes."""
        p = Path(path)
        if p.exists():
            return Path(os.path.realpath(p))
        return p.resolve()

    def _in_write_root(self, path: Path) -> bool:
        """Check whether *path* lives under an allowed write root."""
        str_path = str(path)
        for root in self._write_roots:
            root_path = Path(root).resolve()
            try:
                path.relative_to(root_path)
                return True
            except ValueError:
                continue
        return False

    def _is_denied(self, path: Path) -> bool:
        """Check immutable deny-list."""
        str_path = str(path)
        norm_path = str_path.rstrip("/") + "/" if not str_path.endswith("/") else str_path
        try:
            rel_path = str(path.relative_to(Path.cwd()))
            norm_rel = rel_path.rstrip("/") + "/" if not rel_path.endswith("/") else rel_path
        except ValueError:
            rel_path = str_path
            norm_rel = norm_path
        for denied in self.IMMUTABLE_DENY_LIST:
            norm_denied = denied.rstrip("/") + "/" if not denied.endswith("/") else denied
            if (
                str_path.startswith(denied)
                or rel_path.startswith(denied)
                or norm_path.startswith(norm_denied)
                or norm_rel.startswith(norm_denied)
                or fnmatch.fnmatch(str_path, denied)
                or fnmatch.fnmatch(rel_path, denied)
            ):
                return True
        return False

    def _log(self, operation: str, path: str, allowed: bool, module: str, size_bytes: int | None = None) -> None:
        entry = FileAccessLog(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation=operation,
            path=path,
            allowed=allowed,
            size_bytes=size_bytes,
            module=module,
        )
        if self._audit_log:
            self._audit_log.log("file_access", {"entry": entry.__dict__})

    def _redact(self, data: bytes) -> bytes:
        """Redact secrets/PII from byte data."""
        text = data.decode("utf-8", errors="replace")
        for pattern in self._secret_patterns:
            text = pattern.sub(self._REDACTION_MASK, text)
        return text.encode("utf-8")

    def read(self, path: str | Path, *, module: str) -> bytes:
        """Read a file. Always allowed (except deny-list) but logged."""
        resolved = self._resolve(path)
        if self._is_denied(resolved):
            self._log("read", str(path), False, module)
            raise FileAccessDenied(f"Read denied for immutable path: {path}")
        data = Path(resolved).read_bytes()
        self._log("read", str(path), True, module, len(data))
        return data

    def write(self, path: str | Path, data: bytes, *, module: str, redact: bool = True) -> None:
        """Write *data* to *path*. Blocked if outside write roots.

        If *redact* is True (default), secrets and PII are scrubbed before writing.
        """
        resolved = self._resolve(path)
        if self._is_denied(resolved):
            self._log("write", str(path), False, module)
            raise FileAccessDenied(f"Write denied for immutable path: {path}")
        if not self._in_write_root(resolved):
            self._log("write", str(path), False, module)
            raise FileAccessDenied(f"Write outside allowed roots: {path}")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        safe_data = self._redact(data) if redact else data
        resolved.write_bytes(safe_data)
        self._log("write", str(path), True, module, len(safe_data))

    def delete(self, path: str | Path, *, module: str) -> None:
        """Delete *path*. Blocked if outside write roots or on deny-list."""
        resolved = self._resolve(path)
        if self._is_denied(resolved):
            self._log("delete", str(path), False, module)
            raise FileAccessDenied(f"Delete denied for immutable path: {path}")
        if not self._in_write_root(resolved):
            self._log("delete", str(path), False, module)
            raise FileAccessDenied(f"Delete outside allowed roots: {path}")
        resolved.unlink()
        self._log("delete", str(path), True, module)

    def listdir(self, path: str | Path, *, module: str) -> list[str]:
        """List directory contents. Always allowed but logged."""
        resolved = self._resolve(path)
        if self._is_denied(resolved):
            self._log("list", str(path), False, module)
            raise FileAccessDenied(f"List denied for immutable path: {path}")
        entries = [str(p.name) for p in resolved.iterdir()]
        self._log("list", str(path), True, module)
        return entries

    def mkdir(self, path: str | Path, *, module: str) -> None:
        """Create a directory. Blocked if outside write roots."""
        resolved = self._resolve(path)
        if self._is_denied(resolved):
            self._log("mkdir", str(path), False, module)
            raise FileAccessDenied(f"Mkdir denied for immutable path: {path}")
        if not self._in_write_root(resolved):
            self._log("mkdir", str(path), False, module)
            raise FileAccessDenied(f"Mkdir outside allowed roots: {path}")
        resolved.mkdir(parents=True, exist_ok=True)
        self._log("mkdir", str(path), True, module)

    def redact_buffer(self, data: bytes) -> bytes:
        """Redact secrets from a data buffer without writing to disk."""
        return self._redact(data)
