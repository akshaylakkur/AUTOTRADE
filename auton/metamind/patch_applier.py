"""Applies code changes as git-style diffs with full rollback."""

from __future__ import annotations

import logging
import py_compile
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auton.metamind.rollback_journal import PatchRecord, RollbackJournal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodePatch:
    """A patch to be applied to a file."""

    patch_id: str
    target_file: Path
    diff_text: str
    author: str = "auto"
    reason: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class PatchResult:
    """Result of applying a patch."""

    success: bool
    patch_id: str
    message: str
    rolled_back: bool = False
    test_output: str = ""


@dataclass(frozen=True)
class RollbackResult:
    """Result of a rollback operation."""

    success: bool
    patch_id: str
    message: str


@dataclass(frozen=True)
class DiffHunk:
    """A single hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


class PatchError(Exception):
    """Base exception for patch errors."""


class DiffParseError(PatchError):
    """Failed to parse a unified diff."""


class HunkApplyError(PatchError):
    """Failed to apply a hunk."""


class RollbackError(PatchError):
    """Failed to rollback a patch."""


class TestRunner:
    """Runs pytest on a set of test files."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def run(self, test_files: list[Path]) -> tuple[bool, str]:
        """Run tests and return (passed, output)."""
        if not test_files:
            return True, ""
        args = [sys.executable, "-m", "pytest", "-v"] + [str(f) for f in test_files]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            passed = proc.returncode == 0
            return passed, proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            return False, f"Tests timed out after {self.timeout}s"
        except Exception as exc:
            return False, str(exc)


class PatchApplier:
    """Applies code changes as git-style diffs with full rollback."""

    def __init__(
        self,
        project_root: Path,
        rollback_journal: RollbackJournal,
        test_runner: TestRunner | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.journal = rollback_journal
        self.test_runner = test_runner or TestRunner()

    def apply_patch(self, patch: CodePatch) -> PatchResult:
        """Apply a patch atomically.

        Steps:
        1. Parse diff and apply to a copy.
        2. Write pre-patch snapshot to RollbackJournal.
        3. Apply unified diff to target file.
        4. Run py_compile on modified file.
        5. Run affected tests via TestRunner.
        6. If any step fails, rollback and return failure.
        """
        target = self.project_root / patch.target_file
        original = ""
        if target.exists():
            original = target.read_text(encoding="utf-8")

        # Step 1: parse diff and apply to a copy
        try:
            hunks = self._parse_unified_diff(patch.diff_text)
            patched_lines = self._apply_hunks(original.splitlines(keepends=True), hunks)
            patched = "".join(patched_lines)
        except (DiffParseError, HunkApplyError) as exc:
            return PatchResult(
                success=False,
                patch_id=patch.patch_id,
                message=f"Diff parse/apply error: {exc}",
                rolled_back=False,
            )

        # Step 2: record snapshot
        self.journal.record_snapshot(
            patch_id=patch.patch_id,
            file_path=target,
            content=original,
            author=patch.author,
            reason=patch.reason,
            diff_text=patch.diff_text,
        )

        # Step 3: apply diff to file
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(patched, encoding="utf-8")
        except OSError as exc:
            self._try_rollback(patch.patch_id, target, original)
            return PatchResult(
                success=False,
                patch_id=patch.patch_id,
                message=f"Write error: {exc}",
                rolled_back=True,
            )

        # Step 4: py_compile on result
        try:
            self._compile_check(patched)
        except py_compile.PyCompileError as exc:
            self._try_rollback(patch.patch_id, target, original)
            return PatchResult(
                success=False,
                patch_id=patch.patch_id,
                message=f"Syntax error after write: {exc}",
                rolled_back=True,
            )

        # Step 5: run affected tests
        test_files = self._find_affected_tests(target)
        passed, test_output = self.test_runner.run(test_files)
        if not passed:
            self._try_rollback(patch.patch_id, target, original)
            return PatchResult(
                success=False,
                patch_id=patch.patch_id,
                message="Tests failed after patch",
                rolled_back=True,
                test_output=test_output,
            )

        # Record test results
        self.journal.update_test_result(
            patch.patch_id,
            {"passed": passed, "output": test_output},
        )

        return PatchResult(
            success=True,
            patch_id=patch.patch_id,
            message="Patch applied and tests passed",
            rolled_back=False,
            test_output=test_output,
        )

    def rollback(self, patch_id: str) -> RollbackResult:
        """Restore a file to its pre-patch snapshot."""
        records = self.journal.list_patches()
        record = next((r for r in records if r.patch_id == patch_id), None)
        if record is None:
            return RollbackResult(
                success=False,
                patch_id=patch_id,
                message="Patch not found in journal",
            )

        target = Path(record.file_path)
        snapshot = self.journal.get_snapshot(patch_id, target)
        if snapshot is None:
            return RollbackResult(
                success=False,
                patch_id=patch_id,
                message="Pre-patch snapshot missing",
            )

        try:
            target.write_text(snapshot, encoding="utf-8")
            return RollbackResult(
                success=True,
                patch_id=patch_id,
                message="Rollback successful",
            )
        except OSError as exc:
            return RollbackResult(
                success=False,
                patch_id=patch_id,
                message=f"Rollback failed: {exc}",
            )

    def get_patch_history(self, file_path: str) -> list[PatchRecord]:
        """Return all patches applied to a file, newest first."""
        return self.journal.list_patches(Path(file_path))

    def revert_last(self, file_path: str) -> RollbackResult:
        """Rollback the most recent patch for a file."""
        last = self.journal.get_last_patch(Path(file_path))
        if last is None:
            return RollbackResult(
                success=False,
                patch_id="",
                message="No patches found for file",
            )
        return self.rollback(last.patch_id)

    def _try_rollback(self, patch_id: str, target: Path, original: str) -> None:
        """Best-effort rollback; logs but does not raise on failure."""
        try:
            target.write_text(original, encoding="utf-8")
        except OSError as exc:
            logger.critical(
                "Rollback failed for patch %s on %s: %s",
                patch_id,
                target,
                exc,
            )

    def _compile_check(self, code: str) -> None:
        """Compile-check code via py_compile."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(code)
            tmp_path = Path(tmp.name)
        try:
            py_compile.compile(str(tmp_path), doraise=True)
        finally:
            tmp_path.unlink(missing_ok=True)

    def _parse_unified_diff(self, diff_text: str) -> list[DiffHunk]:
        """Parse a unified diff into hunks."""
        hunks: list[DiffHunk] = []
        lines = diff_text.splitlines(keepends=True)
        i = 0
        while i < len(lines):
            line = lines[i]
            # Match @@ -old_start,old_count +new_start,new_count @@
            m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1
                hunk_lines: list[str] = []
                i += 1
                while i < len(lines):
                    if lines[i].startswith("@@"):
                        break
                    hunk_lines.append(lines[i])
                    i += 1
                hunks.append(
                    DiffHunk(
                        old_start=old_start,
                        old_count=old_count,
                        new_start=new_start,
                        new_count=new_count,
                        lines=hunk_lines,
                    )
                )
                continue
            i += 1

        if not hunks and diff_text.strip():
            # If no hunks found but there is content, it may be a full replacement
            # Treat the entire diff text as a single "hunk" that replaces everything
            hunks.append(
                DiffHunk(
                    old_start=1,
                    old_count=0,
                    new_start=1,
                    new_count=0,
                    lines=[diff_text],
                )
            )
        return hunks

    def _apply_hunks(
        self, original_lines: list[str], hunks: list[DiffHunk]
    ) -> list[str]:
        """Apply hunks to original lines."""
        result = list(original_lines)
        # Process hunks in reverse order to preserve line numbers
        for hunk in sorted(hunks, key=lambda h: h.old_start, reverse=True):
            result = self._apply_single_hunk(result, hunk)
        return result

    def _apply_single_hunk(
        self, lines: list[str], hunk: DiffHunk
    ) -> list[str]:
        """Apply a single hunk."""
        old_idx = hunk.old_start - 1
        new_lines: list[str] = []
        hunk_idx = 0

        while hunk_idx < len(hunk.lines):
            hl = hunk.lines[hunk_idx]
            if hl.startswith("+"):
                new_lines.append(hl[1:])
                hunk_idx += 1
            elif hl.startswith("-"):
                # Skip the corresponding original line
                if old_idx < len(lines):
                    old_idx += 1
                hunk_idx += 1
            elif hl.startswith(" "):
                if old_idx < len(lines):
                    new_lines.append(lines[old_idx])
                    old_idx += 1
                hunk_idx += 1
            elif hl.startswith("\\"):
                # "\ No newline at end of file" — skip
                hunk_idx += 1
            else:
                # Context line without prefix
                if old_idx < len(lines):
                    new_lines.append(lines[old_idx])
                    old_idx += 1
                hunk_idx += 1

        # Replace the old range with new_lines
        before = lines[: hunk.old_start - 1]
        after = lines[hunk.old_start - 1 + hunk.old_count :]
        return before + new_lines + after

    def _find_affected_tests(self, file_path: Path) -> list[Path]:
        """Heuristic: find tests that import the modified module."""
        # Derive module name from file path relative to project root
        try:
            rel = file_path.relative_to(self.project_root)
        except ValueError:
            rel = file_path
        module_parts = list(rel.with_suffix("").parts)
        module_name = ".".join(module_parts)

        tests_dir = self.project_root / "tests"
        if not tests_dir.exists():
            return []

        matches: list[Path] = []
        for test_file in tests_dir.rglob("*.py"):
            content = test_file.read_text(encoding="utf-8")
            # Simple heuristic: look for import statements
            if module_name in content:
                matches.append(test_file)
        return matches
