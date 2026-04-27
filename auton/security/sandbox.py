"""Sandboxed execution for Project ÆON."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from typing import Sequence

from .exceptions import SandboxError


_BOOTSTRAP = """
import builtins
import importlib
import importlib.abc
import json
import sys
import socket

ALLOWED = set(json.loads(sys.argv[1]))

class RestrictedFinder(importlib.abc.MetaPathFinder):
    def find_module(self, fullname, path=None):
        return self.find_spec(fullname, path)

    def find_spec(self, fullname, path, target=None):
        top = fullname.split(".")[0]
        if top in ALLOWED or top in {"builtins", "sys", "importlib", "json", "encodings", "codecs", "io", "abc", "_weakrefset", "types", "warnings", "linecache", "tokenize", "enum", "functools", "collections", "copyreg", "contextlib", "reprlib", "heapq", "keyword", "operator", "reprlib", "types"}:
            return None
        raise ImportError(f"module {fullname!r} is restricted in this sandbox")

sys.meta_path.insert(0, RestrictedFinder())

# Block all network access by monkey-patching socket.socket.
_original_socket = socket.socket
class _BlockedSocket:
    def __init__(self, *args, **kwargs):
        raise OSError("Network access is disabled in the sandbox")

socket.socket = _BlockedSocket

# Purge non-essential modules so imports hit our finder.
ESSENTIAL = {"builtins", "sys", "importlib", "importlib.abc", "json", "socket", "encodings", "codecs", "io", "abc", "_weakrefset", "types", "warnings", "linecache", "tokenize", "enum", "functools", "collections", "copyreg", "contextlib", "reprlib", "heapq", "keyword", "operator"}
for _mod in list(sys.modules.keys()):
    if _mod not in ESSENTIAL and not _mod.startswith("encodings."):
        del sys.modules[_mod]

# Execute user code from stdin.
code = sys.stdin.read()
exec(compile(code, "<sandbox>", "exec"))
"""


@dataclass(frozen=True)
class SandboxResult:
    """Outcome of a sandboxed execution."""

    stdout: str
    stderr: str
    returncode: int
    execution_time: float


class Sandbox:
    """Execute untrusted Python code in a restricted subprocess.

    Network egress is blocked and only modules listed in *allowed_modules*
    may be imported by the guest code.
    """

    def execute(
        self,
        code: str,
        timeout_seconds: int = 30,
        allowed_modules: Sequence[str] | None = None,
    ) -> SandboxResult:
        """Run *code* in a fresh Python interpreter with restrictions.

        :param code: Python source to execute.
        :param timeout_seconds: Maximum wall-clock time for the subprocess.
        :param allowed_modules: Additional modules the guest may import.
        :returns: A :class:`SandboxResult` with captured output and timing.
        :raises SandboxError: If the subprocess cannot be started.
        """
        allowed = list(allowed_modules) if allowed_modules else []
        bootstrap = textwrap.dedent(_BOOTSTRAP).strip()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(bootstrap)
            bootstrap_path = f.name

        try:
            start = time.perf_counter()
            proc = subprocess.run(
                [sys.executable, bootstrap_path, json.dumps(allowed)],
                input=code,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            elapsed = time.perf_counter() - start
        except subprocess.TimeoutExpired as exc:
            elapsed = time.perf_counter() - start
            return SandboxResult(
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                returncode=-9,
                execution_time=elapsed,
            )
        except OSError as exc:
            raise SandboxError(f"Failed to start sandbox process: {exc}") from exc
        finally:
            os.unlink(bootstrap_path)

        return SandboxResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            returncode=proc.returncode,
            execution_time=elapsed,
        )
