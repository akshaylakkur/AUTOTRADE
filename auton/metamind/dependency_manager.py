"""Detects missing imports and manages requirements.txt / pip install."""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Common import name -> PyPI package mapping
_IMPORT_TO_PACKAGE: dict[str, str] = {
    "httpx": "httpx",
    "requests": "requests",
    "aiohttp": "aiohttp",
    "numpy": "numpy",
    "pandas": "pandas",
    "pytest": "pytest",
    "cryptography": "cryptography",
    "uvloop": "uvloop",
    "stripe": "stripe",
    "binance": "python-binance",
    "ccxt": "ccxt",
    "tweepy": "tweepy",
    "praw": "praw",
    "sqlalchemy": "sqlalchemy",
    "pydantic": "pydantic",
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "redis": "redis",
    "celery": "celery",
    "boto3": "boto3",
    "botocore": "botocore",
    "google": "google-api-python-client",
    "openai": "openai",
    "anthropic": "anthropic",
    "tiktoken": "tiktoken",
    "transformers": "transformers",
    "torch": "torch",
    "sklearn": "scikit-learn",
    "matplotlib": "matplotlib",
    "seaborn": "seaborn",
    "plotly": "plotly",
    "yaml": "pyyaml",
    "toml": "toml",
    "jsonschema": "jsonschema",
    "jinja2": "jinja2",
    "watchdog": "watchdog",
    "schedule": "schedule",
    "websockets": "websockets",
    "asyncpg": "asyncpg",
    "psycopg2": "psycopg2-binary",
    "pymongo": "pymongo",
    "click": "click",
    "rich": "rich",
    "typer": "typer",
    "prometheus_client": "prometheus-client",
    "sentry_sdk": "sentry-sdk",
    "structlog": "structlog",
    "orjson": "orjson",
    "msgpack": "msgpack",
    "zstandard": "zstandard",
    "polars": "polars",
    "duckdb": "duckdb",
    "httpx": "httpx",
}


@dataclass(frozen=True)
class ImportErrorInfo:
    """Information about a missing import."""

    import_name: str
    line_number: int
    suggestion: str = ""


@dataclass(frozen=True)
class DependencyReport:
    """Report from dependency resolution."""

    missing_imports: list[str] = field(default_factory=list)
    suggested_packages: dict[str, list[str]] = field(default_factory=dict)
    installed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    total_cost: float = 0.0


class DependencyError(Exception):
    """Base exception for dependency errors."""


class InstallError(DependencyError):
    """Failed to install a package."""


class UnresolvableImportError(DependencyError):
    """Could not resolve an import to a PyPI package."""


class DependencyManager:
    """Detects missing imports and manages requirements.txt / pip install."""

    def __init__(
        self,
        project_root: Path,
        requirements_path: Path | None = None,
        venv_python: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.requirements_path = requirements_path or self.project_root / "requirements.txt"
        self.venv_python = venv_python or Path(sys.executable)

    def _load_requirements(self) -> set[str]:
        """Load installed packages from requirements.txt."""
        if not self.requirements_path.exists():
            return set()
        packages: set[str] = set()
        for line in self.requirements_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Extract package name before any version specifier
            pkg = line.split("=")[0].split("<")[0].split(">")[0].split("[")[0].strip()
            if pkg:
                packages.add(pkg.lower())
        return packages

    def _is_stdlib(self, module_name: str) -> bool:
        """Check if a module is part of the Python standard library."""
        import importlib.util
        import sys

        if module_name in sys.builtin_module_names:
            return True
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            return False
        if spec.origin is None:
            # namespace package or stdlib without origin
            return True
        return "site-packages" not in (spec.origin or "") and "dist-packages" not in (spec.origin or "")

    def scan_for_import_errors(self, code: str) -> list[ImportErrorInfo]:
        """Execute code in a temp subprocess and capture ImportError."""
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
            tmp.write(code)
            tmp_path = Path(tmp.name)

        errors: list[ImportErrorInfo] = []
        try:
            proc = subprocess.run(
                [str(self.venv_python), str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0 and "ImportError" in proc.stderr:
                # Extract import names from stderr
                for line in proc.stderr.splitlines():
                    if "No module named" in line:
                        parts = line.split("'")
                        if len(parts) >= 2:
                            mod = parts[1]
                            errors.append(
                                ImportErrorInfo(
                                    import_name=mod,
                                    line_number=0,
                                    suggestion=self.suggest_packages(mod)[0] if self.suggest_packages(mod) else "",
                                )
                            )
        except subprocess.TimeoutExpired:
            logger.warning("Import error scan timed out")
        except Exception as exc:
            logger.warning("Import error scan failed: %s", exc)
        finally:
            tmp_path.unlink(missing_ok=True)

        return errors

    def scan_module_for_missing_deps(self, module_path: Path) -> list[str]:
        """Parse a module and return third-party imports not in requirements.txt."""
        if not module_path.exists():
            return []
        code = module_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(code, str(module_path))
        except SyntaxError:
            return []

        required = self._load_requirements()
        missing: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".")[0]
                    if not self._is_stdlib(top_level):
                        pkg = _IMPORT_TO_PACKAGE.get(top_level, top_level)
                        if pkg.lower() not in required:
                            missing.append(top_level)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                top_level = mod.split(".")[0]
                if top_level and not self._is_stdlib(top_level):
                    pkg = _IMPORT_TO_PACKAGE.get(top_level, top_level)
                    if pkg.lower() not in required:
                        missing.append(top_level)

        return sorted(set(missing))

    def suggest_packages(self, import_name: str) -> list[str]:
        """Map an import name to candidate PyPI packages."""
        candidates: list[str] = []
        if import_name in _IMPORT_TO_PACKAGE:
            candidates.append(_IMPORT_TO_PACKAGE[import_name])
        candidates.append(import_name)
        return candidates

    def add_requirement(self, package: str, version_spec: str = "") -> bool:
        """Idempotent append to requirements.txt."""
        required = self._load_requirements()
        pkg_name = package.split("=")[0].split("<")[0].split(">")[0].strip().lower()
        if pkg_name in required:
            return False

        line = f"{package}{version_spec}\n"
        with self.requirements_path.open("a", encoding="utf-8") as f:
            f.write(line)
        return True

    def install_requirements(self, dry_run: bool = False) -> subprocess.CompletedProcess:
        """Run `pip install -r requirements.txt` in subprocess."""
        if dry_run:
            return subprocess.run(
                [str(self.venv_python), "-m", "pip", "install", "-r", str(self.requirements_path), "--dry-run"],
                capture_output=True,
                text=True,
            )
        return subprocess.run(
            [str(self.venv_python), "-m", "pip", "install", "-r", str(self.requirements_path)],
            capture_output=True,
            text=True,
        )

    def install_package(self, package: str) -> subprocess.CompletedProcess:
        """Run `pip install <package>` and append to requirements.txt on success."""
        # Retry once with --no-cache-dir on failure
        for attempt in range(2):
            args = [str(self.venv_python), "-m", "pip", "install"]
            if attempt == 1:
                args.append("--no-cache-dir")
            args.append(package)

            proc = subprocess.run(args, capture_output=True, text=True)
            if proc.returncode == 0:
                self.add_requirement(package)
                return proc

            if attempt == 0:
                logger.warning("Pip install failed, retrying with --no-cache-dir: %s", proc.stderr)
                import time
                time.sleep(2)

        raise InstallError(f"Failed to install {package}: {proc.stderr}")

    def resolve_dependencies(self, imports: list[str]) -> DependencyReport:
        """Detect missing imports and install required packages."""
        missing: list[str] = []
        suggested: dict[str, list[str]] = {}
        installed: list[str] = []
        failed: list[str] = []
        total_cost = 0.0

        required = self._load_requirements()
        for imp in imports:
            top = imp.split(".")[0]
            if self._is_stdlib(top):
                continue
            pkg = _IMPORT_TO_PACKAGE.get(top, top)
            if pkg.lower() in required:
                continue
            missing.append(imp)
            candidates = self.suggest_packages(imp)
            suggested[imp] = candidates
            try:
                self.install_package(candidates[0])
                installed.append(candidates[0])
                total_cost += 0.001  # nominal compute cost
            except InstallError as exc:
                logger.error("Failed to install %s: %s", candidates[0], exc)
                failed.append(candidates[0])

        return DependencyReport(
            missing_imports=missing,
            suggested_packages=suggested,
            installed=installed,
            failed=failed,
            total_cost=total_cost,
        )
