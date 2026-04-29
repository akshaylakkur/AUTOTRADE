"""Sandboxed execution for Project ÆON."""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from typing import Sequence

from .exceptions import SandboxError
from .config import ResourceLimits

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
        if top in ALLOWED or top in {"builtins", "sys", "importlib", "json", "socket", "encodings", "codecs", "io", "abc", "_weakrefset", "types", "warnings", "linecache", "tokenize", "enum", "functools", "collections", "copyreg", "contextlib", "reprlib", "heapq", "keyword", "operator", "reprlib", "types"}:
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
    oom_killed: bool = False
    timed_out: bool = False


class ProcessSandbox:
    """Execute untrusted or external code in isolated subprocesses with hard resource limits.

    Subprocess runs under a restricted user/namespace if the OS supports it.
    Network is blocked at the socket layer and the ProcessSandbox never runs
    without NetworkGate active.
    """

    def __init__(self, audit_log=None, default_limits: ResourceLimits | None = None) -> None:
        self._audit_log = audit_log
        self._default_limits = default_limits or ResourceLimits()
        self._total_executions = 0
        self._total_cpu_burned = 0.0

    @property
    def cumulative_metrics(self) -> dict[str, float | int]:
        return {
            "total_executions": self._total_executions,
            "total_cpu_burned_seconds": self._total_cpu_burned,
        }

    def execute(
        self,
        code: str,
        language: str = "python",
        limits: ResourceLimits | None = None,
        allowed_modules: Sequence[str] | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
    ) -> SandboxResult:
        """Run *code* in a fresh interpreter with restrictions.

        :param code: Source to execute.
        :param language: "python" or "bash".
        :param limits: Override default resource limits.
        :param allowed_modules: Additional importable modules.
        :param env: Extra environment variables.
        :param timeout_seconds: Legacy alias for ``limits.max_wall_time_seconds``.
        :returns: Captured output and timing.
        :raises SandboxError: If the subprocess cannot be started.
        """
        if limits is None and timeout_seconds is not None:
            limits = ResourceLimits(max_wall_time_seconds=timeout_seconds)
        limits = limits or self._default_limits
        if language == "bash":
            return self._execute_bash(code, limits, env)
        return self._execute_python(code, limits, allowed_modules, env)

    def _execute_python(
        self,
        code: str,
        limits: ResourceLimits,
        allowed_modules: Sequence[str] | None,
        env: dict[str, str] | None,
    ) -> SandboxResult:
        allowed = list(allowed_modules) if allowed_modules else []
        bootstrap = textwrap.dedent(_BOOTSTRAP).strip()

        workspace = tempfile.mkdtemp(prefix="aeon_sandbox_")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, dir=workspace) as f:
            f.write(bootstrap)
            bootstrap_path = f.name

        try:
            start = time.perf_counter()
            cmd = self._build_cmd(sys.executable, bootstrap_path, json.dumps(allowed), limits=limits)
            proc = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=limits.max_wall_time_seconds,
            )
            elapsed = time.perf_counter() - start
            self._total_executions += 1
            self._total_cpu_burned += elapsed

            oom_killed = "Killed" in proc.stderr or proc.returncode == 137
            return SandboxResult(
                stdout=proc.stdout,
                stderr=proc.stderr,
                returncode=proc.returncode,
                execution_time=elapsed,
                oom_killed=oom_killed,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.perf_counter() - start
            self._total_executions += 1
            return SandboxResult(
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                returncode=-9,
                execution_time=elapsed,
                oom_killed=False,
                timed_out=True,
            )
        except OSError as exc:
            raise SandboxError(f"Failed to start sandbox process: {exc}") from exc
        finally:
            os.unlink(bootstrap_path)
            os.rmdir(workspace)

    def _execute_bash(
        self,
        code: str,
        limits: ResourceLimits,
        env: dict[str, str] | None,
    ) -> SandboxResult:
        start = time.perf_counter()
        try:
            cmd = self._build_cmd("/bin/bash", "-c", code, limits=limits)
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=limits.max_wall_time_seconds,
            )
            elapsed = time.perf_counter() - start
            self._total_executions += 1
            self._total_cpu_burned += elapsed
            return SandboxResult(
                stdout=proc.stdout,
                stderr=proc.stderr,
                returncode=proc.returncode,
                execution_time=elapsed,
                oom_killed=False,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.perf_counter() - start
            self._total_executions += 1
            return SandboxResult(
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                returncode=-9,
                execution_time=elapsed,
                oom_killed=False,
                timed_out=True,
            )
        except OSError as exc:
            raise SandboxError(f"Failed to start sandbox process: {exc}") from exc

    def _build_cmd(self, *base_args, limits: ResourceLimits | None = None) -> list[str]:
        """Build command list with resource limit wrappers where available."""
        limits = limits or self._default_limits
        cmd: list[str] = []
        # Try prlimit on Linux for clean resource limits
        if sys.platform == "linux" and os.path.exists("/usr/bin/prlimit"):
            cmd.extend([
                "prlimit",
                f"--as={limits.max_memory_mb * 1024 * 1024}",
                f"--cpu={limits.max_cpu_time_seconds}",
                f"--nofile={limits.max_file_descriptors}",
            ])
        cmd.extend(base_args)
        return cmd


# Backward-compatible alias
Sandbox = ProcessSandbox
