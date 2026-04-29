"""CI/CD Generator: produces GitHub Actions, Dockerfile, docker-compose, and deploy scripts."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CICDArtifact:
    """A generated CI/CD artifact."""

    name: str
    content: str
    file_path: Path
    artifact_type: str  # "github_action", "dockerfile", "compose", "script"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "content": self.content,
            "file_path": str(self.file_path),
            "artifact_type": self.artifact_type,
            "timestamp": self.timestamp.isoformat(),
        }


class CICDGenerator:
    """Generates CI/CD configuration for SaaS products."""

    def __init__(self, output_dir: Path | str = Path("ci_output")) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # GitHub Actions
    # ------------------------------------------------------------------ #
    def generate_github_actions(
        self,
        project_name: str,
        python_version: str = "3.12",
        test_command: str = "pytest",
        deploy_target: str = "fly.io",
    ) -> CICDArtifact:
        """Generate a GitHub Actions workflow for test + deploy."""
        content = f"""name: CI/CD for {project_name}

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "{python_version}"
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
      - name: Lint with ruff
        run: |
          pip install ruff
          ruff check .
      - name: Run tests
        run: {test_command}

  build:
    runs-on: ubuntu-latest
    needs: test
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: docker build -t {project_name.lower()} .
      - name: Save image
        run: docker save {project_name.lower()} | gzip > {project_name.lower()}.tar.gz
      - uses: actions/upload-artifact@v4
        with:
          name: docker-image
          path: {project_name.lower()}.tar.gz

  deploy:
    runs-on: ubuntu-latest
    needs: build
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Download image artifact
        uses: actions/download-artifact@v4
        with:
          name: docker-image
      - name: Deploy to {deploy_target}
        run: |
          echo "Deploying to {deploy_target}..."
          # Add platform-specific deploy commands here
"""
        path = self.output_dir / ".github" / "workflows" / f"{project_name.lower()}_ci.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        artifact = CICDArtifact(
            name=f"{project_name}_ci",
            content=content,
            file_path=path,
            artifact_type="github_action",
        )
        logger.info("Generated GitHub Actions workflow: %s", path)
        return artifact

    # ------------------------------------------------------------------ #
    # Dockerfile
    # ------------------------------------------------------------------ #
    def generate_dockerfile(
        self,
        project_name: str,
        python_version: str = "3.12",
        entrypoint: str = "main:app",
        port: int = 8000,
    ) -> CICDArtifact:
        """Generate a production Dockerfile."""
        content = f"""# Dockerfile for {project_name}
FROM python:{python_version}-slim

WORKDIR /app

# Security: run as non-root
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \\
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:{port}/health')" || exit 1

EXPOSE {port}

CMD ["python", "-m", "uvicorn", "{entrypoint}", "--host", "0.0.0.0", "--port", "{port}"]
"""
        path = self.output_dir / "Dockerfile"
        path.write_text(content, encoding="utf-8")
        artifact = CICDArtifact(
            name=f"{project_name}_dockerfile",
            content=content,
            file_path=path,
            artifact_type="dockerfile",
        )
        logger.info("Generated Dockerfile: %s", path)
        return artifact

    # ------------------------------------------------------------------ #
    # Docker Compose
    # ------------------------------------------------------------------ #
    def generate_docker_compose(
        self,
        project_name: str,
        services: dict[str, dict[str, Any]] | None = None,
        include_postgres: bool = False,
        include_redis: bool = False,
    ) -> CICDArtifact:
        """Generate a docker-compose.yml for local dev and testing."""
        svc = services or {}
        content_lines = [
            f"services:",
            f"  {project_name.lower()}:",
            f"    build: .",
            f"    ports:",
            f"      - '8000:8000'",
            f"    environment:",
            f"      - DATABASE_URL=${{DATABASE_URL:-sqlite:///data/app.db}}",
            f"      - REDIS_URL=${{REDIS_URL:-redis://redis:6379}}",
        ]

        if include_postgres:
            content_lines.extend([
                f"    depends_on:",
                f"      - db",
                f"  db:",
                f"    image: postgres:16-alpine",
                f"    environment:",
                f"      POSTGRES_USER: app",
                f"      POSTGRES_PASSWORD: app",
                f"      POSTGRES_DB: {project_name.lower()}",
                f"    volumes:",
                f"      - pgdata:/var/lib/postgresql/data",
                f"    ports:",
                f"      - '5432:5432'",
            ])

        if include_redis:
            content_lines.extend([
                f"  redis:",
                f"    image: redis:7-alpine",
                f"    ports:",
                f"      - '6379:6379'",
            ])

        if svc:
            for svc_name, cfg in svc.items():
                content_lines.append(f"  {svc_name}:")
                for k, v in cfg.items():
                    if isinstance(v, list):
                        content_lines.append(f"    {k}:")
                        for item in v:
                            content_lines.append(f"      - {item}")
                    else:
                        content_lines.append(f"    {k}: {v}")

        if include_postgres:
            content_lines.append("volumes:")
            content_lines.append("  pgdata:")

        content = "\n".join(content_lines) + "\n"
        path = self.output_dir / "docker-compose.yml"
        path.write_text(content, encoding="utf-8")
        artifact = CICDArtifact(
            name=f"{project_name}_compose",
            content=content,
            file_path=path,
            artifact_type="compose",
        )
        logger.info("Generated docker-compose.yml: %s", path)
        return artifact

    # ------------------------------------------------------------------ #
    # Deploy scripts
    # ------------------------------------------------------------------ #
    def generate_deploy_script(
        self,
        project_name: str,
        target: str = "fly.io",
    ) -> CICDArtifact:
        """Generate a deployment shell script for a target platform."""
        if target == "fly.io":
            content = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT="{project_name.lower()}"
echo "Deploying $PROJECT to Fly.io..."

if ! command -v flyctl &> /dev/null; then
    echo "Error: flyctl not installed"
    exit 1
fi

if ! flyctl status --app "$PROJECT" &> /dev/null; then
    echo "Creating new Fly.io app..."
    flyctl launch --name "$PROJECT" --region ord --no-deploy
fi

flyctl deploy --app "$PROJECT" --dockerfile Dockerfile
echo "Deployment complete: https://$PROJECT.fly.dev"
"""
        elif target == "railway":
            content = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT="{project_name.lower()}"
echo "Deploying $PROJECT to Railway..."

if ! command -v railway &> /dev/null; then
    echo "Error: Railway CLI not installed"
    exit 1
fi

railway login
railway link --project "$PROJECT" || railway init
railway up

echo "Deployment complete"
"""
        elif target == "render":
            content = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT="{project_name.lower()}"
echo "Deploying $PROJECT to Render..."

curl -X POST "https://api.render.com/v1/services" \\
  -H "Authorization: Bearer $RENDER_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "type": "web_service",
    "name": "'$PROJECT'",
    "region": "oregon",
    "env": "docker"
  }}'

echo "Deployment initiated"
"""
        else:
            content = f"""#!/usr/bin/env bash
set -euo pipefail

PROJECT="{project_name.lower()}"
echo "Deploying $PROJECT to {target}..."
echo "TODO: Add platform-specific deploy commands"
"""

        path = self.output_dir / f"deploy_{target.replace('.', '_')}.sh"
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        artifact = CICDArtifact(
            name=f"{project_name}_deploy_{target}",
            content=content,
            file_path=path,
            artifact_type="script",
        )
        logger.info("Generated deploy script for %s: %s", target, path)
        return artifact

    def generate_full_pipeline(
        self,
        project_name: str,
        python_version: str = "3.12",
        entrypoint: str = "main:app",
        port: int = 8000,
        deploy_targets: list[str] | None = None,
        include_postgres: bool = False,
        include_redis: bool = False,
    ) -> list[CICDArtifact]:
        """Generate a complete CI/CD pipeline for a project."""
        artifacts: list[CICDArtifact] = []
        artifacts.append(
            self.generate_github_actions(
                project_name, python_version, deploy_target=deploy_targets[0] if deploy_targets else "fly.io"
            )
        )
        artifacts.append(
            self.generate_dockerfile(project_name, python_version, entrypoint, port)
        )
        artifacts.append(
            self.generate_docker_compose(
                project_name, include_postgres=include_postgres, include_redis=include_redis
            )
        )
        for target in deploy_targets or ["fly.io"]:
            artifacts.append(self.generate_deploy_script(project_name, target))
        return artifacts
