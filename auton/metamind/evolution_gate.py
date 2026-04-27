"""Evolution Gate: safety sandbox that validates self-modified code before promotion."""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from auton.metamind.dataclasses import EvolutionResult, SafetyRating

logger = logging.getLogger(__name__)

# Forbidden imports / calls that are never allowed in generated code.
_FORBIDDEN_NAMES = frozenset(
    {
        "os.system",
        "os.popen",
        "os.spawn",
        "subprocess.call",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.check_output",
        "subprocess.check_call",
        "socket.socket",
        "socket.create_connection",
        "eval",
        "exec",
        "compile",
        "__import__",
        "importlib.import_module",
        "ctypes.CDLL",
        "ctypes.call",
    }
)


class EvolutionGate:
    """Validates self-modified code before promotion."""

    def __init__(self, sandbox_timeout: float = 10.0) -> None:
        self.sandbox_timeout = sandbox_timeout

    def validate_syntax(self, code: str) -> bool:
        """AST parse check."""
        try:
            ast.parse(code)
            return True
        except SyntaxError as exc:
            logger.warning("Syntax validation failed: %s", exc)
            return False

    def run_sandbox_tests(self, code: str, test_code: str) -> bool:
        """Execute *code* + *test_code* in a restricted subprocess."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as tmp:
            tmp.write(code)
            tmp.write("\n")
            tmp.write(test_code)
            tmp_path = Path(tmp.name)

        try:
            proc = subprocess.run(
                [sys.executable, str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=self.sandbox_timeout,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Sandbox tests failed:\nstdout=%s\nstderr=%s",
                    proc.stdout,
                    proc.stderr,
                )
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.warning("Sandbox tests timed out after %ss", self.sandbox_timeout)
            return False
        finally:
            tmp_path.unlink(missing_ok=True)

    def check_safety(self, code: str) -> tuple[SafetyRating, float, list[str]]:
        """Check for forbidden imports and unsafe patterns."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return SafetyRating.FAIL, 0.0, ["syntax error"]

        issues: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _FORBIDDEN_NAMES or any(
                        alias.name.startswith(f) for f in _FORBIDDEN_NAMES
                    ):
                        issues.append(f"forbidden import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    full = f"{mod}.{alias.name}" if mod else alias.name
                    if full in _FORBIDDEN_NAMES:
                        issues.append(f"forbidden import: {full}")
            elif isinstance(node, ast.Call):
                name = self._call_name(node.func)
                if name in _FORBIDDEN_NAMES:
                    issues.append(f"forbidden call: {name}")

        if issues:
            score = max(0.0, 1.0 - len(issues) * 0.2)
            return SafetyRating.FAIL, round(score, 2), issues

        return SafetyRating.PASS, 1.0, []

    def promote_to_production(
        self, source_path: Path | str, target_path: Path | str
    ) -> EvolutionResult:
        """Move validated code into the main tree."""
        src = Path(source_path)
        dst = Path(target_path)
        if not src.exists():
            return EvolutionResult(
                passed=False,
                safety_score=0.0,
                promoted=False,
                message=f"Source path does not exist: {src}",
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return EvolutionResult(
            passed=True,
            safety_score=1.0,
            promoted=True,
            message=f"Promoted {src} to {dst}",
            source_path=src,
            target_path=dst,
        )

    def validate_and_promote(
        self,
        code: str,
        source_path: Path | str,
        target_path: Path | str,
        test_code: str = "",
    ) -> EvolutionResult:
        """Full gate pipeline: syntax, safety, sandbox, promotion."""
        syntax_ok = self.validate_syntax(code)
        if not syntax_ok:
            return EvolutionResult(
                passed=False,
                safety_score=0.0,
                promoted=False,
                syntax_valid=False,
                message="Syntax validation failed",
            )

        safety_rating, safety_score, issues = self.check_safety(code)
        if safety_rating != SafetyRating.PASS:
            return EvolutionResult(
                passed=False,
                safety_score=safety_score,
                promoted=False,
                syntax_valid=True,
                safety_rating=safety_rating,
                message="; ".join(issues),
            )

        if test_code:
            tests_ok = self.run_sandbox_tests(code, test_code)
            if not tests_ok:
                return EvolutionResult(
                    passed=False,
                    safety_score=safety_score,
                    promoted=False,
                    syntax_valid=True,
                    tests_passed=False,
                    safety_rating=safety_rating,
                    message="Sandbox tests failed",
                )

        return self.promote_to_production(source_path, target_path)

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        """Best-effort extraction of a dotted call name."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{EvolutionGate._call_name(node.value)}.{node.attr}"
        return ""
